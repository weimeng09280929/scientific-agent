"""Fictional bilingual data for offline API and browser verification."""

from core.knowledge.schema import Paper
from core.workspace.reading_session import PassageAnswer


TITLE = "Fictional Reading Study"
PAPER_ID = "guide-unicode"
SENTENCE = "If x ≥ 2, the score may rise by 3 mg; it does not prove causation."
PREVIEW = "导语 😀\n" + SENTENCE + "\n第二处 β\n" + SENTENCE + "\nClosing context."


def seed_passages(s):
    s["store"].save_paper(Paper(
        paper_id=PAPER_ID, title=TITLE, abstract=PREVIEW,
        metadata={"text_origin": "author_abstract"},
    ))
    s["store"].save_paper(Paper(
        paper_id="guide-long", title="Fictional Long Preview",
        abstract="Repeated offline source text. " * 49,
        metadata={"text_origin": "author_abstract"},
    ))


def passage_reply(context, text):
    if text == "翻译选段":
        return PassageAnswer(translation="离线翻译样例。如果 x ≥ 2，评分可能增加 3 mg；这并不能证明因果关系。",
                             terms=["may 表示可能性。"])
    if "Birch" in context["passage"]["text"]:
        return PassageAnswer(explanation="离线解释样例。Birch 只读说明文字。这里没有说明它还会处理表格或图片。")
    return PassageAnswer(explanation="离线解释样例。这句话只描述在条件满足时可能观察到的变化。")
