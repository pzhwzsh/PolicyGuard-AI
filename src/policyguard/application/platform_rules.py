import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformRule:
    code: str
    pattern: str
    reason: str
    suggestion: str
    severity: str = "high"


COMMON_RULES = (
    PlatformRule(
        "COMMON-ABSOLUTE",
        r"(?:100\s*%|百分之百|绝对|永久|零风险|完全无害|行业第一|全网第一)",
        "包含难以证实的绝对化或保证性表述。",
        "改为可验证、有限定条件的客观描述，并补充依据或适用范围。",
    ),
)

PLATFORM_RULES: dict[str, tuple[PlatformRule, ...]] = {
    "taobao": (
        PlatformRule("TAOBAO-EXTREME", r"(?:国家级|最高级|最佳|顶级|第一品牌)", "淘宝商品信息不宜使用极限化、最高级或无法验证的排名表述。", "删除极限词，改写为具体参数、检测结果或用户可核验的商品事实。"),
        PlatformRule("TAOBAO-PRICE", r"(?:原价|最低价|全网最低|最后一天|仅限今天)", "价格或促销表达需要真实依据，虚构原价、绝对低价或紧迫性可能误导消费者。", "使用真实成交价和明确活动期限，避免无法证明的价格比较。"),
    ),
    "douyin": (
        PlatformRule("DOUYIN-MEDICAL", r"(?:治疗|治愈|根治|预防疾病|替代药物|药到病除)", "抖音内容中的医疗功效或疾病治疗承诺属于高风险宣传。", "删除治疗承诺，仅保留经批准或有充分依据的产品功能描述。"),
        PlatformRule("DOUYIN-TRAFFIC", r"(?:加微信|私信付款|站外下单|扫码购买|联系VX)", "可能构成站外引流或绕过平台交易链路。", "改为使用平台允许的商品链接、客服和交易方式。"),
        PlatformRule("DOUYIN-INCOME", r"(?:稳赚|保本|保证收益|月入\s*\d+|轻松赚钱)", "收益保证或低风险高回报表述容易误导用户。", "说明真实条件与风险，不承诺固定收益或必然结果。"),
    ),
    "tiktok_shop": (
        PlatformRule("TIKTOK-MEDICAL", r"(?i)(?:cure|treat|prevent disease|miracle|no side effects|治疗|治愈|根治)", "TikTok Shop 对未经证实的医疗功效和安全保证有严格限制。", "移除疾病治疗或绝对安全承诺，仅保留有证据支持的功能。"),
        PlatformRule("TIKTOK-OFFSITE", r"(?i)(?:whatsapp|telegram|pay outside|external payment|站外付款)", "内容可能引导用户离开平台完成联系或交易。", "使用平台内商品页、客服和支付链路。"),
    ),
    "amazon": (
        PlatformRule("AMAZON-SUPERLATIVE", r"(?i)(?:#\s*1|best seller|best on amazon|top rated|最佳|第一)", "Amazon 上的排名、最佳或平台背书声明需要可验证且不得造成误导。", "删除未经证实的排名或背书，改为客观产品特征。"),
        PlatformRule("AMAZON-CERTIFICATION", r"(?i)(?:FDA approved|FDA certified|guaranteed|100\s*% safe)", "认证、批准或保证性声明需要准确且有可核验依据。", "准确描述认证主体和范围；无法证明时删除批准或保证表述。"),
    ),
    "temu": (
        PlatformRule("TEMU-PRICE", r"(?:全网最低|最低价|原价|仅限今天|最后\s*\d+\s*件)", "虚假折扣、原价或稀缺性表述可能误导消费者。", "展示真实价格、活动期限和库存信息，避免绝对价格比较。"),
        PlatformRule("TEMU-ENDORSEMENT", r"(?:官方指定|平台推荐|Temu官方|政府推荐)", "未经授权不得暗示获得平台或机构背书。", "删除未经证实的官方背书，仅陈述可核验资质。"),
    ),
    "shopee": (
        PlatformRule("SHOPEE-PRICE", r"(?i)(?:lowest price|cheapest|原价|全网最低|最低价)", "价格比较或折扣依据不明时可能构成误导。", "使用真实价格和明确活动条件，删除无法核实的最低价声明。"),
        PlatformRule("SHOPEE-ENDORSEMENT", r"(?i)(?:shopee official|platform recommended|官方指定|平台推荐)", "未经授权的官方或平台背书声明具有误导风险。", "删除平台背书，改为可验证的店铺或商品资质。"),
    ),
}


def evaluate_platform_text(product: dict, platform: str) -> list[dict[str, str]]:
    normalized_platform = (platform or "generic").lower()
    rules = COMMON_RULES + PLATFORM_RULES.get(normalized_platform, ())
    findings: list[dict[str, str]] = []
    for field in ("title", "description"):
        text = str(product.get(field, ""))
        for rule in rules:
            for match in re.finditer(rule.pattern, text):
                findings.append({
                    "rule_code": rule.code,
                    "platform": normalized_platform,
                    "field": field,
                    "matched_text": match.group(0),
                    "reason": rule.reason,
                    "suggestion": rule.suggestion,
                    "severity": rule.severity,
                })
    return findings
