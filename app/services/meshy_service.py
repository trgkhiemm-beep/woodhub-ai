import httpx
import logging
from app.core.config import settings

logger = logging.getLogger("woodhub.meshy")

class MeshyService:
    def __init__(self):
        self.api_key = settings.MESHY_API_KEY
        self.base_url = "https://api.meshy.ai/openapi/v1/image-to-3d"
        self.headers = {"Authorization": f"Bearer {self.api_key}"}
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def create_image_to_3d_task(self, image_url: str) -> str:
        payload = {"image_url": image_url, "enable_pbr": True, "art_style": "realistic"}

        response = await self.client.post(self.base_url, json=payload, headers=self.headers)

        if response.status_code != 202:
            logger.error(
                "Meshy trả về status %s khi tạo task 3D: %s",
                response.status_code, response.text[:500]
            )
            response.raise_for_status()

        result = response.json().get("result")
        if not result:
            raise ValueError(
                f"Meshy trả về 202 nhưng response thiếu field 'result': {response.text[:500]}"
            )

        return result

    async def check_task_status(self, task_id: str) -> dict:
        try:
            response = await self.client.get(
                f"{self.base_url}/{task_id}", headers=self.headers, timeout=10.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                "Meshy trả về lỗi status khi check task %s: %s",
                task_id, e.response.status_code
            )
            return {"status": "FAILED"}
        except httpx.HTTPError as e:
            logger.error("Lỗi mạng khi check trạng thái Meshy cho task %s: %s", task_id, e)
            return {"status": "FAILED"}