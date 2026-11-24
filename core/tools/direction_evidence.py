"""按细分科研问题检索论文；计数只代表检索样本，不代表全领域发文量。"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

from core.tools.arxiv_search import search_arxiv
from core.tools.crossref_search import search_crossref
from core.tools.openalex_search import search_openalex
from core.tools.semantic_scholar import search_semantic_scholar

logger = logging.getLogger(__name__)


FL_DIRECTIONS = (
    ("fl_non_iid", "非独立同分布与个性化联邦学习", "personalized federated learning non IID client heterogeneity"),
    ("fl_privacy", "联邦学习隐私与安全防护", "federated learning privacy secure aggregation poisoning defense"),
    ("fl_efficiency", "通信与计算高效联邦学习", "communication efficient federated learning client selection compression"),
    ("fl_foundation", "联邦基础模型与参数高效微调", "federated learning large language models parameter efficient fine tuning"),
    ("fl_cross_silo", "跨机构联邦学习与真实场景评测", "cross silo federated learning healthcare benchmark domain shift"),
)

FL_SECURITY_DIRECTIONS = (
    ("fl_poisoning", "联邦学习模型投毒与后门防御", "federated learning model poisoning backdoor defense robust aggregation"),
    ("fl_membership_inference", "联邦学习成员推理攻击与隐私审计", "federated learning membership inference attack privacy audit"),
    ("fl_privacy_leak", "梯度泄露与隐私保护", "federated learning gradient inversion privacy leakage differential privacy"),
    ("fl_byzantine", "拜占庭鲁棒聚合与恶意客户端识别", "Byzantine robust federated learning malicious clients aggregation"),
    ("fl_secure_aggregation", "安全聚合与密码学协议", "federated learning secure aggregation homomorphic encryption privacy"),
    ("fl_adversarial", "非独立同分布下的安全评测", "federated learning non IID poisoning defense security benchmark"),
)

LLM_SECURITY_DIRECTIONS = (
    ("llm_jailbreak", "大模型提示注入与越狱攻防", "large language model prompt injection jailbreak attack defense"),
    ("llm_privacy", "大模型训练数据泄露与隐私攻击", "large language model privacy leakage membership inference data extraction"),
    ("llm_poisoning", "大模型数据投毒与后门检测", "large language model data poisoning backdoor attack detection"),
    ("llm_agent_security", "大模型智能体工具调用与权限安全", "large language model agent tool use security prompt injection privilege"),
    ("llm_multimodal_security", "多模态大模型跨模态攻击与防御", "multimodal large language model adversarial attack security defense"),
)

FL_FOCUS = {
    "fl_non_iid": ("个性化", "异构", "non-iid", "non iid", "heterogeneity", "personalized"),
    "fl_privacy": ("隐私", "安全", "投毒", "privacy", "security", "poisoning"),
    "fl_efficiency": ("通信", "压缩", "客户端选择", "communication", "compression", "client selection"),
    "fl_foundation": ("大模型", "语言模型", "微调", "llm", "foundation model", "fine tuning"),
    "fl_cross_silo": ("医疗", "医院", "跨机构", "healthcare", "cross silo", "medical"),
}


def is_federated_learning(interest: str, domain: str = "") -> bool:
    value = f"{interest} {domain}".lower()
    return "联邦学习" in value or "federated learning" in value or bool(re.search(r"\bfederated\s+(?:machine\s+)?learning\b", value))


def is_large_model_security(interest: str, domain: str = "") -> bool:
    value = f"{interest} {domain}".lower()
    model_marker = any(term in value for term in (
        "大模型", "大型语言模型", "语言模型", "llm", "large language model", "foundation model",
    ))
    security_marker = any(term in value for term in (
        "安全", "隐私", "越狱", "提示注入", "投毒", "后门", "攻击", "security", "privacy",
        "jailbreak", "injection", "poison", "backdoor", "adversarial",
    ))
    return model_marker and security_marker


def large_model_security_directions(interest: str, domain: str = "") -> list[dict]:
    """大模型安全使用可复用的标准学术检索式，不依赖 LLM 先生成 JSON。"""
    value = f"{interest} {domain}".lower()
    focus = {
        "llm_jailbreak": ("越狱", "注入", "jailbreak", "injection"),
        "llm_privacy": ("隐私", "泄露", "privacy", "leak"),
        "llm_poisoning": ("投毒", "后门", "poison", "backdoor"),
        "llm_agent_security": ("智能体", "工具", "agent", "tool"),
        "llm_multimodal_security": ("多模态", "图像", "multimodal", "vision"),
    }
    selected = list(LLM_SECURITY_DIRECTIONS)
    matched = [entry for entry in selected if any(term in value for term in focus[entry[0]])]
    if matched:
        selected = matched + [entry for entry in selected if entry not in matched]
    return [{"id": key, "name": name, "query": query} for key, name, query in selected[:5]]


def fallback_directions(interest: str, domain: str, english_query: str) -> list[dict]:
    """模型规划失败时保留真实检索能力；名称由用户主题生成，不制造热度结论。"""
    query = re.sub(r"[^A-Za-z0-9\- ]+", " ", english_query or "").strip()
    if not query:
        return []
    label = interest.strip() or domain.strip() or "研究主题"
    return [
        {"id": "fallback_methods", "name": f"{label}的核心方法与公开评测", "query": f"{query} methods benchmark evaluation"},
        {"id": "fallback_robustness", "name": f"{label}的鲁棒性与失效边界", "query": f"{query} robustness limitations failure"},
        {"id": "fallback_applications", "name": f"{label}的真实场景应用与可部署性", "query": f"{query} real world application deployment"},
    ]


def federated_directions(interest: str, domain: str = "") -> list[dict]:
    """有明确子兴趣时优先检索对应问题，避免所有联邦学习请求返回同一组方向。"""
    value = f"{interest} {domain}".lower()
    security = any(term in value for term in (
        "网络空间安全", "网络安全", "信息安全", "cybersecurity", "cyber security",
        "security", "安全", "隐私", "投毒", "后门",
        # 成员推理/推断本质是隐私攻击研究，路由到联邦安全方向表
        "成员推理", "成员推断", "membership",
    ))
    if security:
        selected = list(FL_SECURITY_DIRECTIONS)
        focus = {
            "fl_membership_inference": ("成员推理", "成员推断", "membership inference", "membership"),
            "fl_poisoning": ("投毒", "后门", "poison", "backdoor"),
            "fl_privacy_leak": ("隐私", "泄露", "梯度", "privacy", "leak", "gradient"),
            "fl_byzantine": ("拜占庭", "恶意客户端", "byzantine", "malicious client"),
            "fl_secure_aggregation": ("密码", "安全聚合", "加密", "encryption", "secure aggregation"),
            "fl_adversarial": ("评测", "异构", "non iid", "benchmark"),
        }
        matched = [entry for entry in selected if any(term in interest.lower() for term in focus[entry[0]])]
        if matched:
            selected = matched + [entry for entry in selected if entry not in matched][:2]
        return [{"id": key, "name": name, "query": query} for key, name, query in selected]
    matched = {key for key, terms in FL_FOCUS.items() if any(term in value for term in terms)}
    selected = [entry for entry in FL_DIRECTIONS if not matched or entry[0] in matched]
    if matched:
        selected += [entry for entry in FL_DIRECTIONS if entry[0] not in matched][:2]
    return [{"id": key, "name": name, "query": query} for key, name, query in selected]


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))


def fetch_zh_evidence(zh_query: str, *, per_source: int = 8) -> Optional[dict]:
    """中文文献检索补充：OpenAlex / S2 均索引了中文期刊论文的标题与摘要。

    用用户的中文原词直接检索，返回与英文方向同构的证据条目
    （id=direction_zh），供推荐时合并中英文两路证据。失败返回 None。
    """
    query = (zh_query or "").strip()
    if not _has_cjk(query):
        return None
    found: list[dict] = []
    for source, search in (("openalex", search_openalex), ("semantic_scholar", search_semantic_scholar)):
        try:
            papers = search(query=query, max_results=per_source, timeout=12)
        except Exception as exc:
            logger.warning("中文文献检索失败（source=%s, query=%r）: %s", source, query, exc)
            continue
        for paper in papers:
            meta = paper.to_meta_dict()
            if not (meta.get("title") and meta.get("url")):
                continue
            meta["source"] = source
            meta["language"] = "zh"
            found.append(meta)
    seen: set = set()
    papers: list[dict] = []
    for paper in found:
        key = (paper.get("doi") or paper.get("arxiv_id")
               or re.sub(r"\W+", "", paper["title"].lower())).lower()
        if key in seen:
            continue
        seen.add(key)
        papers.append(paper)
    if not papers:
        return None
    recent = sum(1 for p in papers if isinstance(p.get("year"), int) and p["year"] >= datetime.now().year - 2)
    return {
        "id": "direction_zh", "name": f"中文文献视角：{query}", "query": query,
        "sample_count": len(papers), "recent_sample_count": recent,
        "papers": papers[:12],
    }


def fetch_direction_evidence(directions: list[dict], *, per_source: int = 8) -> list[dict]:
    """每个问题分别检索 arXiv/S2，去重并保留可核验的论文 URL。"""
    output = []
    current_year = datetime.now().year
    for index, direction in enumerate(directions):
        query = direction["query"].strip()
        if not query:
            continue
        found = []
        # Crossref 作为主源（无需密钥且 DOI 可核验）。只有结果不足时才依次
        # 请求 OpenAlex / arXiv / S2，避免限流源拖慢每个细分方向。
        sources = [("crossref", search_crossref)]
        fallback_sources = [("openalex", search_openalex), ("arxiv", search_arxiv)]
        if index == 0:
            fallback_sources.append(("semantic_scholar", search_semantic_scholar))
        for source, search in sources + fallback_sources:
            if source != "crossref" and len(found) >= 2:
                break
            try:
                papers = search(query=query, max_results=per_source, timeout=12)
            except Exception as exc:
                logger.warning("方向论文源调用失败（source=%s, query=%r）: %s", source, query, exc)
                papers = []
            for paper in papers:
                meta = paper.to_meta_dict()
                if meta.get("title") and meta.get("url") and _matches_direction(meta, direction):
                    meta["source"] = source
                    found.append(meta)
        if not found and len(query.split()) > 4:
            try:
                relaxed_query = " ".join(query.split()[:4])
                for source, search in (("crossref", search_crossref), ("openalex", search_openalex)):
                    for paper in search(query=relaxed_query, max_results=per_source, timeout=12):
                        meta = paper.to_meta_dict()
                        if meta.get("title") and meta.get("url") and _matches_direction(meta, direction):
                            meta["source"] = source
                            found.append(meta)
            except Exception as exc:
                logger.warning("宽松方向检索失败（query=%r）: %s", query, exc)
        seen = set()
        papers = []
        for paper in found:
            key = (paper.get("doi") or paper.get("arxiv_id") or re.sub(r"\W+", "", paper["title"].lower())).lower()
            if key in seen:
                continue
            seen.add(key)
            papers.append(paper)
        recent = sum(1 for p in papers if isinstance(p.get("year"), int) and p["year"] >= current_year - 2)
        if papers:
            output.append({
                "id": direction["id"], "name": direction["name"], "query": query,
                "sample_count": len(papers), "recent_sample_count": recent,
                "papers": papers[:12],
            })
    return output


def _matches_direction(paper: dict, direction: dict) -> bool:
    """拒绝仅因模糊检索而混入的无关论文；这是弱相关性门槛，不是质量评分。"""
    text = f"{paper.get('title', '')} {paper.get('abstract', '')}".lower()
    query = direction["query"].lower()
    if direction["id"].startswith("fl_"):
        if "federat" not in text:
            return False
        markers = {
            "fl_membership_inference": ("membership inference", "member inference", "inference attack", "privacy audit", "privacy risk"),
            "fl_poisoning": ("poison", "backdoor", "attack", "robust"),
            "fl_privacy_leak": ("privacy", "gradient", "inversion", "leak"),
            "fl_byzantine": ("byzantine", "malicious", "robust", "aggregat"),
            "fl_secure_aggregation": ("secure aggregat", "encrypt", "cryptograph", "privacy"),
            "fl_adversarial": ("non-iid", "non iid", "poison", "security", "attack"),
            "fl_non_iid": ("non-iid", "non iid", "personaliz", "heterogen"),
            "fl_privacy": ("privacy", "secure", "poison", "attack"),
            "fl_efficiency": ("communication", "compress", "client selection"),
            "fl_foundation": ("language model", "foundation model", "fine tuning", "lora"),
            "fl_cross_silo": ("cross-silo", "cross silo", "healthcare", "medical"),
        }.get(direction["id"], ())
        return any(marker in text for marker in markers)
    if direction["id"].startswith("llm_"):
        if not any(marker in text for marker in ("large language model", "language model", "llm", "foundation model")):
            return False
        markers = {
            "llm_jailbreak": ("prompt injection", "jailbreak", "adversarial prompt"),
            "llm_privacy": ("privacy", "membership inference", "data extraction", "training data"),
            "llm_poisoning": ("poison", "backdoor", "malicious data"),
            "llm_agent_security": ("agent", "tool use", "tool-use", "privilege", "prompt injection"),
            "llm_multimodal_security": ("multimodal", "vision-language", "image", "cross-modal"),
        }.get(direction["id"], ())
        return any(marker in text for marker in markers)
    terms = [term for term in re.findall(r"[a-z]{4,}", query) if term not in {"study", "research", "method", "model", "learning", "based"}]
    return sum(term in text for term in set(terms)) >= min(2, len(set(terms)))


TOPIC_MARKERS = {
    "元学习": ("meta-learning", "meta learning"),
    "动量": ("momentum",),
    "差分隐私": ("differential privacy",),
    "非iid": ("non-iid", "non iid", "non independent and identically distributed"),
    "后门": ("backdoor",),
    "梯度反演": ("gradient inversion",),
    "成员推理": ("membership inference", "member inference", "成员推理", "成员推断"),
    "成员推断": ("membership inference", "member inference", "成员推理", "成员推断"),
    "联邦学习": ("federat", "联邦"),
    "拜占庭": ("byzantine",),
    "同态加密": ("homomorphic encryption",),
    "安全聚合": ("secure aggregation",),
}


def supporting_papers_for_topic(topic: str, papers: list[dict]) -> list[dict]:
    """具体术语必须逐项在论文标题或摘要中出现，否则不能作为已支撑选题。"""
    lowered = topic.lower()
    required = [markers for phrase, markers in TOPIC_MARKERS.items() if phrase in lowered]
    if not required:
        return papers[:3]
    matched = []
    for markers in required:
        hits = [paper for paper in papers if any(marker in f"{paper.get('title', '')} {paper.get('abstract', '')}".lower() for marker in markers)]
        if not hits:
            return []
        for paper in hits:
            if paper not in matched:
                matched.append(paper)
    return matched[:3]
