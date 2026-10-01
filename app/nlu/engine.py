"""
NLU engine: message (+ ngữ cảnh) → NLUResult.

Thứ tự tin cậy:
  1. Xác nhận/hủy và lệnh thay đổi dữ liệu: parser deterministic (không bao giờ qua LLM).
  2. Intent + tách ý: LLM (nếu bật), đối chiếu với bộ phân loại dự phòng.
  3. Entity: luôn trích xuất bằng code từ đoạn câu tương ứng.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

from app.nlp.vietnamese import normalize_vietnamese_chat
from app.nlu import rules
from app.nlu.extract import extract, is_english
from app.nlu.lexicon import Lexicon, fold
from app.nlu.llm import LLMIntentClassifier, LLMUnavailable
from app.nlu.mutations import CANCEL_RE, CODE_RE, INJECTION, parse_mutation, plain
from app.nlu.schema import MUTATION_INTENTS, Entities, Intent, IntentFrame, NLUResult

logger = logging.getLogger("woodhub.nlu")
VN_TZ = timezone(timedelta(hours=7))

TOOL_TO_INTENT = {
    "update_product_price": Intent.UPDATE_PRICE, "update_product_description": Intent.UPDATE_DESCRIPTION,
    "adjust_inventory": Intent.ADJUST_INVENTORY, "update_store_info": Intent.UPDATE_STORE_INFO,
    "upsert_faq": Intent.UPSERT_FAQ, "create_promotion": Intent.CREATE_PROMOTION,
    "set_promotion_status": Intent.SET_PROMOTION_STATUS, "upsert_category": Intent.UPSERT_CATEGORY,
    "upsert_material": Intent.UPSERT_MATERIAL,
}
INTENT_TO_TOOL = {v: k for k, v in TOOL_TO_INTENT.items()}
_WEAK = {Intent.UNCLEAR, Intent.OUT_OF_SCOPE, Intent.GREETING}
_ENTITY_DRIVEN = {Intent.PRODUCT_DETAIL, Intent.INVENTORY, Intent.COMPARE, Intent.RECOMMEND, Intent.STORE_INFO,
                  Intent.POLICY, Intent.BRANCHES, Intent.PROMOTION}


def _merge(target: Entities, source: Entities) -> Entities:
    data = target.model_dump()
    for k, v in source.model_dump().items():
        if data.get(k) in (None, [], "") and v not in (None, [], ""):
            data[k] = v
    return Entities(**data)


class NLUEngine:
    def __init__(self, lexicon: Lexicon, llm: LLMIntentClassifier | None = None):
        self.lexicon = lexicon
        self.llm = llm

    async def parse(self, message: str, *, context: str | None = None, has_context: bool = False,
                    today: date | None = None) -> NLUResult:
        today = today or datetime.now(VN_TZ).date()
        raw = (message or "").strip()
        p = plain(raw)
        folded = fold(raw)
        injection = any(re.search(rx, p) for rx in INJECTION)
        lang = "en" if is_english(raw) else "vi"

        m = CODE_RE.match(p)
        if m:
            return NLUResult(frames=[IntentFrame(intent=Intent.CONFIRM)], source="rules", injection_suspected=injection,
                             confirm_code=(m.group(1) or "").upper() or None, language=lang)
        if CANCEL_RE.match(p):
            return NLUResult(frames=[IntentFrame(intent=Intent.CANCEL)], source="rules", injection_suspected=injection, language=lang)

        whole = extract(raw, self.lexicon)
        u = normalize_vietnamese_chat(raw)["normalized_input"]
        mutation = parse_mutation(raw, u, p, whole.product_codes, today)
        if mutation is not None:
            return NLUResult(frames=[IntentFrame(intent=TOOL_TO_INTENT[mutation.name], entities=whole, tool_args=mutation.args)],
                             source="rules", injection_suspected=injection, language=lang)

        # Deterministic trước: domain guard + phân loại theo tín hiệu. LLM chỉ được gọi khi bộ luật không chắc chắn
        # (tiết kiệm quota; câu ngoài phạm vi bị từ chối trước khi tới LLM).
        frames, confident = self._rules_frames(raw, folded, whole, has_context)
        if confident or self.llm is None:
            return NLUResult(frames=frames, source="rules", injection_suspected=injection, language=lang)
        try:
            pairs, llm_lang, raw_out = await self.llm.classify(raw, context)
        except LLMUnavailable as exc:
            logger.warning("LLM NLU unavailable → rules fallback: %s", str(exc)[:200])
            return NLUResult(frames=frames, source="rules", injection_suspected=injection, language=lang)
        frames = self._dedupe([self._frame_from_llm(intent, span, whole, has_context, raw, u, p, today)
                               for intent, span in pairs])
        return NLUResult(frames=frames, source="llm+rules", injection_suspected=injection,
                         language=llm_lang if llm_lang != "vi" else lang, llm_raw=raw_out)

    # ------------------------------------------------------------------ helpers
    def _frame_from_llm(self, intent: Intent, span: str, whole: Entities, has_context: bool,
                        raw: str, u: str, p: str, today: date) -> IntentFrame:
        ents = extract(span, self.lexicon) if span != raw else whole
        if intent in (Intent.PRODUCT_DETAIL, Intent.INVENTORY, Intent.RECOMMEND, Intent.PRODUCT_SEARCH, Intent.COMPARE):
            ents = _merge(ents, whole)
        if intent in MUTATION_INTENTS:
            # LLM nghĩ là thay đổi dữ liệu nhưng parser không trích được tham số → hỏi lại, không đoán
            return IntentFrame(intent=intent, entities=ents, tool_args={"_needs": "details"})
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
