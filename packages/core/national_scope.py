import re

def province_only_scope(question: str) -> bool:
    pass
    text = str(question or "")
    if re.search(r"只(?:算|看|比较|包括)?省(?:份)?|仅(?:限|看|比较)?省(?:份)?|不(?:含|包含|包括|计入|算)直辖市|排除直辖市", text):
        return True
    if re.search(r"省级地区|省级行政区|(?:包含|包括|含)直辖市|省(?:和|及|与|、)直辖市|省市", text):
        return False
    return bool(re.search(r"省份|各省|哪(?:个|一)?省|(?:最高|最好|榜首|第一)(?:的|是)?省", text))
