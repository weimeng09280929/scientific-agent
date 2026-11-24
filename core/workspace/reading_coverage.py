"""Bounded bilingual retrieval hints for the points explicitly asked about."""

import re
from typing import NamedTuple


class Point(NamedTuple):
    key: str
    question: str
    asked: re.Pattern
    signals: re.Pattern
    strong: re.Pattern


def _point(key, question, asked, signals, strong):
    return Point(key, question, re.compile(asked, re.I), re.compile(signals, re.I), re.compile(strong, re.I))


_POINTS = (
    _point("order", "先后有哪些步骤，各做什么？",
           r"先.{1,48}(?:再|后|接着)|前后两步|前一步|后一步|分[几两三多]步|两(?:步|个阶段)|\b(?:first.{3,90}then|stages?|sequence|pipeline)\b",
           r"\b(?:phase|stage|step|then|followed by|before|after|first|second)\b|阶段|先.{1,32}再",
           r"\b(?:first|initial|candidate|pretrain|stage)\w*.{3,145}\b(?:then|followed by|second|rerank)\w*|\b(?:apply|use|begin|start)\b.{0,80}\bthen\b|\b(?:two[- ]stage|sequentially)\b|先.{1,60}再"),
    _point("inputs", "每一步分别使用什么输入、数据或材料？",
           r"哪(?:一)?批|什么(?:样的)?(?:数据|材料|语料|文字|文本|输入)|各(?:自)?用(?:的)?什么|用哪些|数据从哪|(?:两次|两步).{0,14}(?:数据|材料|语料)|\b(?:what data|which (?:data|corpus|inputs?)|input to|data source)\b",
           r"\b(?:input|training set|corpus|corpora|dataset|task data|training data|domain data)\b|输入|训练集|语料",
           r"\b(?:pre[- ]?train\w*|train\w*|adapt\w*)\s+(?:(?:[\w-]+\s+){0,4})(?:on|using|with)\b.{0,90}\b(?:data|corpus|text|sentences?|set)\b|\b(?:takes? as input|unlabeled training set|unlabeled domain)\b|以.{1,20}为输入"),
    _point("labels", "数据是否使用标签或人工标注？分别说明。",
           r"标注|标签|人工标|\b(?:labels?|annotation|labeled|unlabeled)\b",
           r"\b(?:unlabel(?:l)?ed|label(?:l)?ed|supervised|annotation|labels)\b|标注|有监督|无监督",
           r"\b(?:unlabel(?:l)?ed|without (?:manual )?(?:labels|annotation)|human annotat\w*)\b|无需标注|无标注"),
    _point("prediction_target", "分别预测什么，训练目标或答案从哪里来？",
           r"猜什么|预测(?:什么|的目标)|(?:训练)?答案.{0,10}(?:从哪|怎么来)|目标(?:值)?从哪|\b(?:predict what|what .{0,28} predict|target labels?)\b",
           r"\b(?:predict\w*|target|label|mask\w*|sample\w*)\b|预测|采样|标签",
           r"\b(?:predict\w*|reconstruct\w*)\b.{0,65}\b(?:original|masked|target|whether)\b|\b(?:actual|random)\b.{1,40}\b(?:examples?|sentences?|tokens?|labels?)\b"),
    _point("representations", "所问步骤中对象各用什么表示，如何运算？",
           r"表示|向量|编码|重排|再排序|打分|\b(?:representation|embedding|rerank\w*|scor\w*)\b",
           r"\b(?:embedding|binary|representation|code|score|inner product|Hamming)\b|表示|向量|内积",
           r"\b(?:inner product|Hamming distance|distance between|similarity between)\b|内积|汉明距离"),
    _point("output", "各步骤返回什么，范围是什么？",
           r"返回|输出|保留多少|留下什么|\b(?:return|output|top[- ]?k)\b",
           r"\b(?:return\w*|output\w*|select\w*|top[- ]?[a-z0-9]+)\b|返回|输出",
           r"\b(?:select|obtain|return|retrieve)\w*\b.{0,45}\b(?:top[- ]?[a-z0-9]+|output|result)\b|返回.{0,16}(?:结果|列表|候选)"),
    _point("positive", "正例怎样形成？",
           r"正(?:样本|例|对)|\bpositive (?:pairs?|examples?|samples?)\b|同一句.{0,16}(?:两次|两遍|成对)",
           r"\bpositive (?:pairs?|examples?|instances?)\b|正例|正样本",
           r"\b(?:two (?:forward|views)|identical positive pairs|positive pair takes|predicts? (?:the )?input.{1,40}itself)\b|正例.{0,12}(?:构造|来自)"),
    _point("negative", "负例或反例来自哪里？",
           r"负(?:样本|例|对)|反例|\bnegatives?\b",
           r"\bnegatives?\b|负例|负样本",
           r"\b(?:in[- ]batch negatives?|negatives? (?:come|are|from|sampled|drawn)|contradiction pairs)\b|负(?:例|样本).{0,15}(?:来自|采样)"),
    _point("regime", "所问设定与另一种监督方式有什么区别？",
           r"(?:无监督|自监督).{0,100}(?:有监督|监督版)|(?:有监督|监督版).{0,100}(?:无监督|自监督)|\bunsupervised\b.{0,100}\bsupervised\b",
           r"\b(?:unsupervised|supervised|self[- ]supervised)\b|无监督|有监督",
           r"\bunsupervised\b.{2,500}\bsupervised\b|\bsupervised\b.{2,500}\bunsupervised\b|无监督.{1,80}有监督"),
    _point("motivation", "作者为什么做这个设计？区分动机、实测结果与因果解释。",
           r"为什么|为何|目的是什么|有什么用|好处|\b(?:why|rationale|motivation|purpose|advantage)\b",
           r"\b(?:memory|storage|cost|efficient|compress\w*|overhead|accuracy|motivation|benefit)\b|内存|存储|成本|效率|动机",
           r"\b(?:reduce|save|minimize)\w*\b.{0,55}\b(?:memory|storage|cost|overhead)\b|\b(?:memory|storage|index(?: size)?)\b.{0,55}\b(?:binary|compact|compress\w*)\b|\bstor\w*\b.{0,40}\bas (?:binary|compact)\b|节省.{0,12}(?:存储|内存)"),
)
_PROCESS = re.compile(r"训练|预训|继续练|微调|编码|拿什么|怎么做|怎么跑|怎么接|接下来|原理|流程|"
                      r"\b(?:train\w*|fine[- ]?tun\w*|workflow|procedure|how .{0,32} work)\b", re.I)
_PROCESS_QUESTION = re.compile(r"怎么|怎样|如何|具体做了什么|过程|猜什么|预测什么|\bhow\b|\bworkflow\b", re.I)
_PROCESS_SOURCE = re.compile(r"\b(?:pre[- ]?train\w*|train\w*|adaptation|fine[- ]?tun\w*|input|outputs?|"
                             r"candidate|rerank\w*|encode\w*|algorithm|construct\w*|procedure)\b|训练|方法|输入|输出", re.I)
_HEAD = re.compile(r"(?im)^\s*(?:\d+\.?){1,4}\s+[A-Za-z].{1,80}\b(?:training|retrieval|methods?|model|approach|inference)\b")
_BACKGROUND = re.compile(r"(?im)^\s*\d+\s+(?:background|related work|discussion|conclusion)\b")
_METHOD_DEFINITION = re.compile(
    r"\b(?:we (?:continue|perform|apply|use|construct)|our (?:approach|method)|refers to|is defined as)\b"
    r".{0,105}\b(?:pre[- ]?train\w*|train\w*|adapt\w*|unlabeled|input|corpus|retriev\w*)\b", re.I,
)
_DIRECT_SEQUENCE = re.compile(
    r"\b(?:begin|start)\s+with\b.{0,100}\b(?:apply|train|use|run)\b.{1,55}\bthen\b|"
    r"\b(?:two[- ]stage approach|two[- ]stage method)\b", re.I,
)
_REPRESENTATION_REASON = re.compile(
    r"\b(?:stor\w*|compress\w*)\b.{0,55}\b(?:binary|compact|low[- ]precision|sparse)\b|"
    r"\b(?:memory|storage|index size)\b.{0,55}\b(?:binary|compact|compress\w*)\b", re.I,
)
_AUGMENTED_CONTEXT = re.compile(r"\b(?:augment(?:ed|ing) (?:training data|corpus)|select\w* additional (?:data|text))\b", re.I)
_DATA_SCOPE = {
    "domain_data": re.compile(r"\bunlabel(?:l)?ed (?:domain[- ]specific|in[- ]domain|target[- ]domain)\b", re.I),
    "task_data": re.compile(r"\bunlabel(?:l)?ed (?:task[- ]specific|training set.{0,24}\b(?:given|target|specific) task)\b", re.I),
}
_REFERENCES = re.compile(r"(?im)^\s*(?:references|acknowledg(?:e)?ments|bibliography)\s*$")
_OBJECTIVE_HEADING = re.compile(r"\b(?:task|objective)\s*#?\s*\d+\s*:[^.!?]{0,100}$", re.I)
_LATER_OBJECTIVE = re.compile(r"\b(?:task|objective)\s*#?\s*\d+\s*:", re.I)
_OBJECTIVE_NAME = re.compile(r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9]{1,7}(?![A-Za-z0-9_])")
_PREDICTION_DETAILS = (
    _point("masked_token_target", "遮盖位置要预测什么，正确词从哪里来？",
           r"(?:遮(?:住|盖|挡)|掩码|盖住).{0,10}(?:词|字|位置)|\bmasked tokens?\b",
           r"\b(?:mask\w*|corrupt\w*)\b",
           r"\b(?:predict\w*|reconstruct\w*)\b.{0,95}\b(?:original|masked)\b.{0,32}\b(?:token\w*|word\w*|input)\b"),
    _point("sentence_pair_target", "句对要预测什么，正反例从哪里来？",
           r"(?:两句(?:话|子)?|句对|下一句)|\b(?:sentence pair|next sentence)\b",
           r"\b(?:sentences?|pair)\b",
           r"\b(?:actual|true)\b.{0,40}\b(?:sentences?|pairs?)\b.{0,220}\brandom\b.{0,35}\b(?:sentences?|pairs?)\b"),
)


def asked_points(text):
    points = [point for point in _POINTS if point.asked.search(text)]
    if any(point.key == "prediction_target" for point in points) and not _OBJECTIVE_NAME.search(text):
        points.extend(point for point in _PREDICTION_DETAILS if point.asked.search(text))
    return points


def procedural(text):
    keys = {p.key for p in asked_points(text)}
    if "prediction_target" in keys or (_PROCESS.search(text) and _PROCESS_QUESTION.search(text)):
        return True
    return bool(keys & {"order", "inputs", "labels", "positive", "negative", "representations", "output"}) and (
        bool(_PROCESS.search(text)) or len(keys - {"motivation"}) >= 2
    )


def named_targets(points, question):
    if not any(p.key == "prediction_target" for p in points):
        return {}
    names = list(dict.fromkeys(_OBJECTIVE_NAME.findall(question)))[:4]
    return {"term:" + name: name for name in names}


def _objective_definition(text, mention, strong):
    following = _LATER_OBJECTIVE.split(text[mention.end():mention.end() + 460], maxsplit=1)[0]
    if strong.search(following):
        return True
    preceding = text[max(0, mention.start() - 240):mention.start()]
    # A new heading introduces a different task. Earlier text may define the
    # preceding task and cannot serve as the new definition.
    return not _OBJECTIVE_HEADING.search(preceding) and bool(strong.search(preceding))


def coverage_signals(points, text, normalized, question="", *, names=None):
    """Search hints are associations; the model and evaluator read the raw span."""
    if _REFERENCES.search(text):
        return {}, 0
    context = (int(bool(_HEAD.search(text))) + int(bool(_PROCESS_SOURCE.search(normalized)))
               + 2 * int(bool(_METHOD_DEFINITION.search(normalized))) - 2 * int(bool(_BACKGROUND.search(text)))
               - 2 * int(bool(_AUGMENTED_CONTEXT.search(normalized)) and not re.search(
                   r"增强|扩充|增广|\baugment", question, re.I)))
    scores = {p.key: (3 if p.strong.search(normalized) else 1)
              for p in points if p.signals.search(normalized) or p.strong.search(normalized)}
    if "order" in scores and _DIRECT_SEQUENCE.search(normalized):
        scores["order"] = 6
    if "motivation" in scores and _REPRESENTATION_REASON.search(normalized):
        scores["motivation"] = 6
    if {"order", "inputs"} <= {p.key for p in points}:
        for key, pattern in _DATA_SCOPE.items():
            if pattern.search(normalized):
                scores[key] = 6
    if anchors := names if names is not None else named_targets(points, question):
        objective = next(p for p in points if p.key == "prediction_target")
        for mention in _OBJECTIVE_NAME.finditer(normalized):
            key = "term:" + mention[0]
            if key in anchors:
                strength = 6 if _objective_definition(normalized, mention, objective.strong) else 1
                scores[key] = max(scores.get(key, 0), strength)
    return scores, context


def covered_points(points, windows):
    return {p.key: [entry["excerpt_id"] for entry, scores in windows if scores.get(p.key, 0) >= 3]
            for p in points}
