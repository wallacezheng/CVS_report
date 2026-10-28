"""HTML 解析小工具：以「商品連結」為錨點，收集到下一個商品連結前的文字。"""
from __future__ import annotations

import re
from bs4 import BeautifulSoup, NavigableString, Tag


def item_blocks(html: str, href_pattern: re.Pattern) -> list[tuple[Tag, str]]:
    """回傳 [(商品名稱連結, 該商品區塊的文字)]。

    官網常見結構是「圖片連結 + 名稱連結 + 價格/日期/地區文字」重複排列，
    所以以有文字的商品連結為起點，往後收集文字直到下一個商品連結。
    """
    soup = BeautifulSoup(html, "html.parser")
    anchors = [a for a in soup.find_all("a", href=True)
               if href_pattern.search(a["href"]) and a.get_text(strip=True)]
    anchor_ids = {id(a) for a in anchors}
    blocks = []
    for a in anchors:
        texts = []
        for el in a.next_elements:
            if isinstance(el, Tag) and el.name == "a" and id(el) in anchor_ids and el is not a:
                break
            if isinstance(el, Tag) and el.name == "a" and el.get("href") and \
                    href_pattern.search(el["href"]) and el is not a and not el.get_text(strip=True):
                # 下一個商品的圖片連結
                break
            if isinstance(el, NavigableString) and el.parent is not a:
                t = el.strip()
                if t:
                    texts.append(t)
            if len(texts) > 40:
                break
        blocks.append((a, "\n".join(texts)))
    return blocks
