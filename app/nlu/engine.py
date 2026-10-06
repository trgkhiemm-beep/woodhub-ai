"""
NLU engine: message (+ ngữ cảnh) → NLUResult.

Thứ tự (deterministic trước, LLM sau):
  1. Yêu cầu THAY ĐỔI dữ liệu → CHANGE_REQUEST (agent chỉ đọc; không có tham số, không có tool ghi).
  2. Domain guard + phân loại theo tín hiệu entity; câu ngoài phạm vi bị từ chối trước khi tới LLM.
  3. LLM (nếu bật) chỉ khi bộ luật không chắc chắn: intent + tách ý; entity luôn trích xuất bằng code.
"""
from __future__ import annotations

import logging

from app.nlp.vietnamese import normalize_vietnamese_chat
from app.nlu import rules
from app.nlu.extract import extract, is_english
from app.nlu.lexicon import Lexicon, fold
from app.nlu.llm import LLMIntentClassifier, LLMUnavailable
from app.nlu.patterns import injection_suspected, is_change_request, plain
from app.nlu.schema import Entities, Intent, IntentFrame, NLUResult

logger = logging.getLogger("woodhub.nlu")

_WEAK = {Intent.UNCLEAR, Intent.OUT_OF_SCOPE, Intent.GREETING}
_ENTITY_DRIVEN = {Intent.PRODUCT_DETAIL, Intent.INVENTORY, Intent.COMPARE, Intent.RECOMMEND, Intent.SUPPLIER_INFO,
                  Intent.POLICY, Intent.BRANCHES, Intent.PROMOTION, Intent.ORDER_STATUS}


def _merge(target: Entities, source: Entities) -> Entities:
    data = target.model_dump()
    for k, v in source.model_dump().items():
        if data.get(k) in (None, [], "", False) and v not in (None, [], "", False):
            data[k] = v
    return Entities(**data)


class NLUEngine:
    def __init__(self, lexicon: Lexicon, llm: LLMIntentClassifier | None = None):
        self.lexicon = lexicon
        self.llm = llm

    async def parse(self, message: str, *, context: str | None = None, has_context: bool = False) -> NLUResult:
        raw = (message or "").strip()
        p = plain(raw)
        folded = fold(raw)
        injection = injection_suspected(p)
        lang = "en" if is_english(raw) else "vi"
        whole = extract(raw, self.lexicon)

        if is_change_request(normalize_vietnamese_chat(raw)["normalized_input"].lower()) or is_change_request(p):
            return NLUResult(frames=[IntentFrame(intent=Intent.CHANGE_REQUEST, entities=whole)], source="rules",
                             injection_suspected=injection, language=lang)

        frames, confident = self._rules_frames(raw, folded, whole, has_context)
        if confident or self.llm is None:
            return NLUResult(frames=frames, source="rules", injection_suspected=injection, language=lang)
        try:
            pairs, llm_lang, raw_out = await self.llm.classify(raw, context)
        except LLMUnavailable as exc:
            logger.warning("LLM NLU unavailable → rules fallback: %s", str(exc)[:200])
            return NLUResult(frames=frames, source="rules", injection_suspected=injection, language=lang)
        frames = self._dedupe([self._frame_from_llm(intent, span, whole, has_context, raw) for intent, span in pairs])
        return NLUResult(frames=frames, source="llm+rules", injection_suspected=injection,
                         language=llm_lang if llm_lang != "vi" else lang, llm_raw=raw_out)

    # ------------------------------------------------------------------ helpers
    def _frame_from_llm(self, intent: Intent, span: str, whole: Entities, has_context: bool, raw: str) -> IntentFrame:
        ents = extract(span, self.lexicon) if span != raw else whole
        if intent in (Intent.PRODUCT_DETAIL, Intent.INVENTORY, Intent.RECOMMEND, Intent.PRODUCT_SEARCH, Intent.COMPARE):
            ents = _merge(ents, whole)
        if intent == Intent.CHANGE_REQUEST:
            return IntentFrame(intent=intent, entities=ents)  # chỉ dẫn tới câu trả lời "trợ lý chỉ đọc"
        rule_intent = rules.classify(fold(span), ents, has_context=has_context)
        many = len(ents.product_codes) >= 2 or len(ents.ordinals) >= 2
        if normalize_vietnamese_chat(span)["is_gibberish"] and not (ents.product_codes or ents.category):
            intent = Intent.UNCLEAR  # chuỗi vô nghĩa → hỏi lại (không kết luận ngoài phạm vi)
        elif intent == Intent.DESIGN_TASK and not ents.task_id:
            intent = rule_intent     # "hướng dẫn tạo mẫu 3D" không phải tra trạng thái task
        elif intent == Intent.POLICY and not ents.policy_type and rule_intent == Intent.GUIDE_FAQ:
            intent = rule_intent
        elif many and intent in (Intent.RECOMMEND, Intent.PRODUCT_DETAIL, Intent.PRODUCT_SEARCH) and rule_intent == Intent.COMPARE:
            intent = rule_intent
        elif intent in _WEAK and rule_intent not in _WEAK:
            intent = rule_intent  # LLM bỏ sót nhưng có tín hiệu entity rõ ràng
        elif intent == Intent.PRODUCT_SEARCH and rule_intent in _ENTITY_DRIVEN and (
                ents.product_codes or ents.reference or ents.ordinal or ents.relative or ents.seats or ents.size):
            intent = rule_intent
        elif intent == Intent.PRODUCT_DETAIL and not (ents.product_codes or ents.reference or ents.ordinal or has_context
                                                      or ents.product_name) and rule_intent in (Intent.RECOMMEND, Intent.PRODUCT_SEARCH):
            intent = rule_intent  # không có sản phẩm cụ thể để xem chi tiết
        return IntentFrame(intent=intent, entities=ents)

    def _rules_frames(self, raw: str, folded: str, whole: Entities, has_context: bool) -> tuple[list[IntentFrame], bool]:
        """(frames, tự_tin). Câu ngoài phạm vi (cả câu) → từ chối ngay, không tách ý / không gọi LLM."""
        if rules.out_of_scope(folded, whole):
            return [IntentFrame(intent=Intent.OUT_OF_SCOPE, entities=whole)], True
        clauses = rules.split_compare(folded) if rules.is_compare(folded) else rules.split_clauses(folded)
        frames: list[IntentFrame] = []
        confident = True
        for clause in clauses:
            ents = extract(clause, self.lexicon) if len(clauses) > 1 else whole
            # ý phụ trong câu nhiều ý ("..., còn hàng không?") tham chiếu sản phẩm ở ý trước
            intent, sure = rules.classify_conf(clause, ents, has_context=has_context or bool(whole.product_codes))
            if intent == Intent.UNCLEAR and frames:
                frames[-1].entities = _merge(frames[-1].entities, ents)
                continue
            confident = confident and sure
            if frames and intent in (Intent.PRODUCT_DETAIL, Intent.INVENTORY) and not (ents.product_codes or ents.reference or ents.ordinal):
                ents = _merge(ents, Entities(product_codes=whole.product_codes))
            frames.append(IntentFrame(intent=intent, entities=ents))
        if len(frames) > 1:
            frames = [f for f in frames if f.intent != Intent.UNCLEAR] or frames[:1]
        frames = self._dedupe(frames)
        if not frames:
            return [IntentFrame(intent=Intent.UNCLEAR, entities=whole)], False
        return frames, confident

    @staticmethod
    def _dedupe(frames: list[IntentFrame]) -> list[IntentFrame]:
        seen, out = set(), []
        for f in frames:
            key = (f.intent, tuple(f.entities.product_codes), f.entities.category, f.entities.policy_type, f.entities.ordinal)
            if key not in seen:
                seen.add(key)
                out.append(f)
        return out[:4]
