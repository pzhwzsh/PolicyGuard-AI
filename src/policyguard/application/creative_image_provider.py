"""Provider-neutral product-scene image editing contract."""

import base64
import io
from dataclasses import dataclass, field

from policyguard.application.provider_http import ProviderRetryPolicy, post_with_retry


@dataclass(frozen=True, slots=True)
class ProductSceneEditProvider:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 180
    retry_policy: ProviderRetryPolicy = field(default_factory=ProviderRetryPolicy)

    def edit(
        self,
        *,
        source: bytes,
        prompt: str,
        negative_prompt: str,
        width: int,
        height: int,
    ) -> tuple[bytes, dict]:
        response = post_with_retry(
            self.base_url.rstrip("/") + "/product-scene-edit",
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            json={
                "model": self.model,
                "source_image_base64": base64.b64encode(source).decode("ascii"),
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "width": width,
                "height": height,
                "preserve_product_identity": True,
            },
            timeout=self.timeout_seconds,
            policy=self.retry_policy,
        )
        payload = response.json()
        try:
            content = base64.b64decode(payload["image_base64"], validate=True)
        except (KeyError, ValueError) as exc:
            raise ValueError("creative_provider_image_invalid") from exc
        from PIL import Image

        try:
            image = Image.open(io.BytesIO(content))
            image.verify()
        except Exception as exc:
            raise ValueError("creative_provider_image_invalid") from exc
        return content, {
            "provider_model": self.model,
            "provider_attempts": response.extensions.get("policyguard_attempts", 1),
            "provider_seed": payload.get("seed"),
        }
