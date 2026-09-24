# -*- coding: utf-8 -*-
"""S6 桥接：东财行业名 → 新浪行业名 映射 + 新浪行业资金流抓取。

CI 版只保留「映射表 + 资金流抓取」两部分（bt_run 只依赖 s6_flow.json 与
EM2SINA，不再做合并写回，去掉对 s3e_pool/s4_pool 的依赖）。

用法：
    python s6_bridge.py          # 抓新浪行业资金流 → s6_flow.json
或作为模块：
    from s6_bridge import EM2SINA, fetch_flow, write_flow
"""
import os, json, ssl, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
     "Referer": "https://vip.stock.finance.sina.com.cn/"}

# ---------- 东财行业 -> 新浪行业 ----------
EM2SINA = {
    # TMT
    "文化传媒": "传媒娱乐", "互联网服务": "电子信息", "软件服务": "电子信息",
    "游戏": "传媒娱乐", "半导体": "电子器件", "电子元件": "电子器件",
    "光学光电子": "电子器件", "消费电子": "电子器件", "通讯行业": "电子信息",
    "电信运营": "电子信息", "计算机设备": "电子信息", "IT设备": "电子信息",
    # 制造
    "专用设备": "机械行业", "通用设备": "机械行业", "机械行业": "机械行业",
    "机械设备": "机械行业", "工程机械": "机械行业", "交运设备": "机械行业",
    "汽车行业": "汽车制造", "汽车零部件": "汽车制造", "汽车服务": "汽车制造",
    "电机": "电器行业", "电源设备": "电器行业", "风电设备": "发电设备",
    "光伏设备": "发电设备", "电池": "电子器件", "仪器仪表": "仪器仪表",
    "航天航空": "飞机制造", "船舶制造": "船舶制造", "造纸印刷": "造纸行业",
    "包装材料": "印刷包装", "家用轻工": "家具行业", "木业家具": "家具行业",
    "家电行业": "家电行业", "纺织服装": "纺织行业", "化纤行业": "化纤行业",
    # 材料能源
    "化学制品": "化工行业", "化学原料": "化工行业", "化工行业": "化工行业",
    "化肥行业": "农药化肥", "农药兽药": "农药化肥", "塑胶制品": "塑料制品",
    "塑料制品": "塑料制品", "橡胶制品": "塑料制品", "有色金属": "有色金属",
    "钢铁行业": "钢铁行业", "煤炭行业": "煤炭行业", "石油行业": "石油行业",
    "燃气": "供水供气", "公用事业": "供水供气", "电力行业": "电力行业",
    "玻璃玻纤": "玻璃行业", "水泥建材": "水泥行业", "装修建材": "建筑建材",
    "工程建设": "建筑建材", "建筑装饰": "建筑建材", "装修装饰": "建筑建材",
    "房地产开发": "房地产", "房地产": "房地产", "房地产服务": "房地产",
    # 消费医药
    "医药制造": "生物制药", "中药": "生物制药", "生物制品": "生物制药",
    "医疗器械": "医疗器械", "医疗服务": "医疗器械", "食品饮料": "食品行业",
    "酿酒行业": "酿酒行业", "农牧饲渔": "农林牧渔", "农林牧渔": "农林牧渔",
    "商业百货": "商业百货", "贸易行业": "物资外贸", "物流行业": "交通运输",
    "航运港口": "交通运输", "航空机场": "交通运输", "交通运输": "交通运输",
    "旅游酒店": "酒店旅游", "教育": "其它行业", "专业服务": "其它行业",
    # 金融
    "银行": "金融行业", "证券": "金融行业", "保险": "金融行业",
    "多元金融": "金融行业", "金融服务": "金融行业",
    # 其他
    "环保工程": "环保行业", "环保行业": "环保行业", "园林工程": "环保行业",
}


def get_json(url, timeout=15, retry=3):
    for a in range(retry):
        try:
            req = urllib.request.Request(url, headers=H)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                t = r.read().decode("gbk", "replace")
            i, j = t.find("["), t.rfind("]")
            if i < 0:
                return None
            return json.loads(t[i:j + 1])
        except Exception:
            if a == retry - 1:
                return None
            time.sleep(1.0 + a)
    return None


def fetch_flow():
    """拉新浪行业资金流，返回 {行业名: {net, ratio, in, out}}。"""
    flow = {}
    for asc in (0, 1):
        for page in range(1, 4):
            arr = get_json("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                           "MoneyFlow.ssl_bkzj_bk?page=%d&num=100&sort=netamount&asc=%d&fenlei=0"
                           % (page, asc))
            if not arr:
                break
            for it in arr:
                if it.get("name"):
                    flow[it["name"]] = {
                        "net": float(it.get("netamount") or 0),
                        "ratio": it.get("ratioamount"),
                        "in": float(it.get("inamount") or 0),
                        "out": float(it.get("outamount") or 0),
                    }
            if len(arr) < 100:
                break
            time.sleep(0.4)
    return flow


def write_flow(path=None):
    flow = fetch_flow()
    p = path or os.path.join(HERE, "s6_flow.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(flow, f, ensure_ascii=False, indent=1)
    print("新浪行业资金流: %d 个 → %s" % (len(flow), os.path.basename(p)))
    return p


if __name__ == "__main__":
    write_flow()
