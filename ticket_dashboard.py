#!/usr/bin/env python3
"""ticket_dashboard.py — TimePad + TicketsCloud — Джейн Фест 2026"""

import requests, json, os, time
from datetime import datetime, date, timedelta
from collections import defaultdict
from math import ceil

# ─── НАСТРОЙКИ ─────────────────────────────────────────────
# Токены берутся из переменных окружения (облако GitHub).
# Значения справа от "or" — запасные, для запуска на своём Mac.
TIMEPAD_TOKEN     = os.environ.get("TIMEPAD_TOKEN")     or "2bd6b751bb1fc54d88c08f4d572fc3ee4a62cd6d"
TIMEPAD_EVENT_ID  = int(os.environ.get("TIMEPAD_EVENT_ID") or 4000854)

TICKETSCLOUD_KEY      = os.environ.get("TICKETSCLOUD_KEY")      or "3aae0f2b6bf74740aa3bbb7f237c7485"
TICKETSCLOUD_EVENT_ID = os.environ.get("TICKETSCLOUD_EVENT_ID") or "6a26dd07ab6e4f670c65c0e1"
TC_SERVICE_FEE        = 1.1   # сервисный сбор TicketsCloud 10%

FEST_DATE  = date(2026, 7, 18)   # дата фестиваля
PLAN_J     = 4000                # план Джейноман
PLAN_P     = 350                 # план Поклонник
PRICE_J    = 1200.0
PRICE_P    = 5000.0

OUTPUT_DIR  = os.path.dirname(os.path.abspath(__file__))
# В облаке путь задаётся через DASHBOARD_OUTPUT, локально — dashboard.html рядом
OUTPUT_FILE = os.environ.get("DASHBOARD_OUTPUT") or os.path.join(OUTPUT_DIR, "dashboard.html")
# ───────────────────────────────────────────────────────────


# ══════════════════════════════════════════════════════════
#  TIMEPAD
# ══════════════════════════════════════════════════════════

def tp_h():
    # Браузерные заголовки: помогают пройти анти-бот фильтры,
    # которые блокируют "не браузерные" запросы с серверных IP.
    return {
        "Authorization": f"Bearer {TIMEPAD_TOKEN}",
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://timepad.ru/",
        "Origin": "https://timepad.ru",
    }

def get_timepad_event():
    try:
        r = requests.get(f"https://api.timepad.ru/v1/events/{TIMEPAD_EVENT_ID}",
                         headers=tp_h(), timeout=15)
        print(f"  [TimePad event] HTTP {r.status_code}")
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        print(f"  Ошибка: {e}"); return None

def get_timepad_orders():
    url, params, all_orders = \
        f"https://api.timepad.ru/v1/events/{TIMEPAD_EVENT_ID}/orders", \
        {"limit": 100, "skip": 0}, []
    while True:
        try:
            r = requests.get(url, headers=tp_h(), params=params, timeout=15)
            print(f"  [TimePad orders skip={params['skip']}] HTTP {r.status_code}")
            if r.status_code == 429:
                time.sleep(5); continue
            if r.status_code != 200: break
            data  = r.json()
            batch = data.get("values", [])
            total = data.get("total", 0)
            all_orders.extend(batch)
            params["skip"] += len(batch)
            if params["skip"] >= total or not batch: break
            time.sleep(0.3)
        except Exception as e:
            print(f"  Ошибка: {e}"); break
    return all_orders

def parse_timepad(event, orders):
    # paid/notpaid — статусы TimePad. Оплаченные = paid.
    PAID   = {"paid", "transfer_complete", "executed", "ok"}
    CANCEL = {"refunded", "refund", "deleted", "rejected"}
    ename  = event.get("name", "Мероприятие") if event else "Мероприятие"
    sales  = []
    for order in orders:
        st = order.get("status", {})
        st_name = st.get("name", "") if isinstance(st, dict) else str(st)
        cancelled = st_name in CANCEL
        # Берём только оплаченные и отменённые (notpaid/booked игнорируем)
        if st_name not in PAID and not cancelled:
            continue
        # Дата оплаты из payment.paid_at, иначе created_at
        pay = order.get("payment", {})
        paid_at = (pay.get("paid_at") if isinstance(pay, dict) else None) or order.get("created_at", "")
        date_str = paid_at[:10] if paid_at else None
        for ticket in order.get("tickets", []):
            price = float(ticket.get("price_nominal", 0) or 0)
            tt    = ticket.get("ticket_type", {})
            tname = tt.get("name", "") if isinstance(tt, dict) else ""
            # Определяем тип по названию билета TimePad
            low = tname.lower()
            if "джейноман" in low:
                cat = "Джейноман"
            elif "поклонник" in low:
                cat = "Поклонник"
            else:
                # запасной вариант — по цене
                cat = "Джейноман" if abs(price - PRICE_J) < abs(price - PRICE_P) else "Поклонник"
            sales.append({"source": "TimePad", "event": ename, "date": date_str,
                          "category": cat, "price": price, "cancelled": cancelled})
    return sales


# ══════════════════════════════════════════════════════════
#  TICKETSCLOUD  — тип билета определяем по цене
# ══════════════════════════════════════════════════════════

def tc_h(): return {"Authorization": f"key {TICKETSCLOUD_KEY}"}

def tc_get(path, params=None):
    try:
        r = requests.get(f"https://ticketscloud.com{path}",
                         headers=tc_h(), params=params, timeout=15)
        print(f"  [TC] {path} → HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"  {r.text[:200]}"); return None
        return r
    except Exception as e:
        print(f"  Ошибка: {e}"); return None

def get_tc_event():
    r = tc_get(f"/v1/resources/events/{TICKETSCLOUD_EVENT_ID}")
    if not r: return None
    d = r.json()
    return d.get("data", d) if isinstance(d, dict) else None

def get_tc_orders():
    params, all_orders = {"page":1,"limit":100,"event":TICKETSCLOUD_EVENT_ID}, []
    while True:
        r = tc_get("/v1/resources/orders", params=params)
        if not r: break
        raw   = r.json()
        batch = raw if isinstance(raw,list) else raw.get("data",[])
        all_orders.extend(batch)
        if isinstance(raw,list): break
        pg    = raw.get("pagination",{})
        pages = pg.get("pages",1) if isinstance(pg,dict) else 1
        if params["page"] >= pages or not batch: break
        params["page"] += 1
    return all_orders

def tc_detect_type(value_str, qty):
    """Определяем тип билета по сумме заказа и количеству."""
    if qty == 0: return []
    try:
        val = float(value_str or 0)
        price_per = val / qty / TC_SERVICE_FEE
        # Округляем к ближайшей известной цене
        if abs(price_per - PRICE_J) < abs(price_per - PRICE_P):
            return [("Джейноман", PRICE_J)] * qty
        else:
            return [("Поклонник", PRICE_P)] * qty
    except:
        return [("Билет", 0.0)] * qty

def parse_tc(event, orders):
    ename = "Мероприятие TicketsCloud"
    if event:
        name = event.get("name",{})
        ename = (name.get("ru") or name.get("en") or ename
                 if isinstance(name,dict) else str(name))
    sales = []
    for order in orders:
        status   = order.get("status","")
        tix_ids  = order.get("tickets", [])
        qty      = len(tix_ids)
        cancelled = status in ("canceled",)
        if status in ("expired",) or (not tix_ids and not cancelled):
            continue
        created  = order.get("created_at","") or ""
        date_str = created[:10] or None
        value    = order.get("value","0")
        detected = tc_detect_type(value, qty if qty else 0)
        for cat, price in detected:
            sales.append({"source":"TicketsCloud","event":ename,"date":date_str,
                          "category":cat,"price":price,"cancelled":cancelled})
    return sales


# ══════════════════════════════════════════════════════════
#  АГРЕГАЦИЯ
# ══════════════════════════════════════════════════════════

def surge_weight(d):
    """Относительная интенсивность продаж за d дней до события.
    Отражает разгон продаж фестивалей в финале (последние 4-6 недель):
    основная масса билетов уходит в последние 2-4 недели и финальные дни.
    Значения можно калибровать под свои данные прошлых мероприятий."""
    if d > 42: return 1.0    # >6 недель — базовый фон
    if d > 28: return 1.3    # 4-6 недель — начало разгона
    if d > 14: return 1.9    # 2-4 недели
    if d > 7:  return 2.6    # последняя-предпоследняя неделя
    if d > 3:  return 3.5    # финальная неделя
    return 5.0               # последние 72 часа — пик

def forecast_type(by_dc, sd, cat, sold, plan, today, days_left):
    """Прогноз продаж одного типа билета с учётом сезонного ускорения."""
    daily = {d: by_dc[d].get(cat, {}).get("tickets", 0) for d in sd}

    def window_sum(a, b):
        s = 0
        for k in range(a, b):
            s += daily.get(str(today - timedelta(days=k)), 0)
        return s

    last7  = window_sum(0, 7)
    prev7  = window_sum(7, 14)
    last14 = last7 + prev7
    rate7  = last7 / 7.0
    rate14 = last14 / 14.0
    # базовый темп: среднее за 14 дней (если истории мало — за всё время)
    base_rate = rate14 if last14 > 0 else (sold / max(len(sd), 1))
    wow = (last7 / prev7) if prev7 > 0 else (2.0 if last7 > 0 else 1.0)

    # пик продаж за один день
    peak_date, peak_val = "—", 0
    for d, v in daily.items():
        if v > peak_val:
            peak_val, peak_date = v, d

    # Консервативный: текущий темп без ускорения
    low = int(sold + base_rate * days_left)

    # С учётом сезонного разгона: каждый оставшийся день взвешен кривой всплеска
    surge_add = 0.0
    for i in range(days_left):
        surge_add += base_rate * surge_weight(days_left - i)
    high = int(sold + surge_add)
    if high < low:
        high = low

    # Ограничиваем абсурдно большие числа (если тип уже сильно опережает план)
    cap = int(plan * 3) if plan else high
    low_c, high_c = min(low, cap), min(high, cap)
    expected = int(round((low_c + high_c) / 2))

    required = max((plan - sold) / days_left, 0)   # нужный темп в день
    pct_low  = round(min(low_c  / plan * 100, 999)) if plan else 0
    pct_high = round(min(high_c / plan * 100, 999)) if plan else 0

    if low_c >= plan:
        verdict, vcls = "✓ План закрыт", "ok"
    elif high_c >= plan:
        verdict, vcls = "● На грани плана", "mid"
    else:
        verdict, vcls = "⚠ Отставание", "warn"

    return {
        "sold": sold, "plan": plan,
        "rate7": round(rate7, 1), "rate14": round(rate14, 1),
        "wow_pct": int(round((wow - 1) * 100)),
        "peak_date": peak_date, "peak_val": peak_val,
        "low": low_c, "high": high_c, "expected": expected,
        "capped": high > cap,
        "required": round(required, 1),
        "pct_low": pct_low, "pct_high": pct_high,
        "gap": max(plan - expected, 0),
        "surplus": max(expected - plan, 0),
        "verdict": verdict, "vcls": vcls,
        "on_pace": base_rate >= required,
    }

def aggregate(all_sales):
    tot_t = sum(1 for s in all_sales if not s["cancelled"])
    tot_r = sum(s["price"] for s in all_sales if not s["cancelled"])

    by_day    = defaultdict(lambda:{"tickets":0,"revenue":0.0,"cancelled":0})
    by_ds     = defaultdict(lambda:defaultdict(lambda:{"tickets":0,"revenue":0.0}))
    by_dc     = defaultdict(lambda:defaultdict(lambda:{"tickets":0,"revenue":0.0,"cancelled":0}))
    by_src    = defaultdict(lambda:{"tickets":0,"revenue":0.0})
    by_cat    = defaultdict(lambda:{"tickets":0,"revenue":0.0})
    by_sc     = defaultdict(lambda:defaultdict(lambda:{"tickets":0,"revenue":0.0}))

    for s in all_sales:
        d   = s["date"] or "?"
        src = s["source"]
        cat = s["category"]
        p   = s["price"]
        cl  = s["cancelled"]

        if d != "?":
            by_day[d]["cancelled" if cl else "tickets"] += 1
            if not cl: by_day[d]["revenue"] += p
            by_ds[d][src]["tickets"] += (0 if cl else 1)
            by_ds[d][src]["revenue"] += (0 if cl else p)
            by_dc[d][cat]["cancelled" if cl else "tickets"] += 1
            if not cl: by_dc[d][cat]["revenue"] += p

        if not cl:
            by_src[src]["tickets"] += 1
            by_src[src]["revenue"] += p
            by_cat[cat]["tickets"] += 1
            by_cat[cat]["revenue"] += p
            by_sc[src][cat]["tickets"] += 1
            by_sc[src][cat]["revenue"] += p

    sd = sorted(d for d in by_day if d != "?")

    # ── ПРОГНОЗ с учётом сезонного ускорения продаж ──────────
    today     = date.today()
    days_left = max((FEST_DATE - today).days, 1)
    sold_j    = by_cat.get("Джейноман", {}).get("tickets", 0)
    sold_p    = by_cat.get("Поклонник", {}).get("tickets", 0)

    fc_j = forecast_type(by_dc, sd, "Джейноман", sold_j, PLAN_J, today, days_left)
    fc_p = forecast_type(by_dc, sd, "Поклонник", sold_p, PLAN_P, today, days_left)

    return {
        "total_tickets": tot_t,
        "total_revenue": tot_r,
        "avg_check":     tot_r / tot_t if tot_t else 0,
        "by_day":        {d: dict(by_day[d]) for d in sd},
        "by_day_source": {d: dict(by_ds[d]) for d in sd},
        "by_day_cat":    {d: {c: dict(v) for c,v in by_dc[d].items()} for d in sd},
        "by_source":     dict(by_src),
        "by_category":   dict(by_cat),
        "by_src_cat":    {src: dict(cats) for src, cats in by_sc.items()},
        "updated_at":    datetime.now().strftime("%d.%m.%Y %H:%M"),
        "fest_date":     str(FEST_DATE),
        "days_left":     days_left,
        "sold_j": sold_j, "sold_p": sold_p,
        "plan_j": PLAN_J, "plan_p": PLAN_P,
        "fc_j": fc_j, "fc_p": fc_p,
    }


# ══════════════════════════════════════════════════════════
#  HTML
# ══════════════════════════════════════════════════════════

def generate_html(m):
    days = list(m["by_day"].keys())

    def ds(src, key):
        return [m["by_day_source"].get(d,{}).get(src,{}).get(key,0) for d in days]
    def dc(cat, key):
        return [m["by_day_cat"].get(d,{}).get(cat,{}).get(key,0) for d in days]

    tpT = ds("TimePad","tickets"); tcT = ds("TicketsCloud","tickets")
    tpR = ds("TimePad","revenue"); tcR = ds("TicketsCloud","revenue")
    jT  = dc("Джейноман","tickets"); pT = dc("Поклонник","tickets")
    jR  = dc("Джейноман","revenue"); pR = dc("Поклонник","revenue")
    jC  = dc("Джейноман","cancelled"); pC = dc("Поклонник","cancelled")
    allC = [m["by_day"].get(d,{}).get("cancelled",0) for d in days]

    sL = list(m["by_source"]); sT = [m["by_source"][s]["tickets"] for s in sL]
    cat_j_t = m["by_category"].get("Джейноман", {}).get("tickets", 0)
    cat_p_t = m["by_category"].get("Поклонник", {}).get("tickets", 0)
    bsc = m["by_src_cat"]

    # Таблица типов по площадкам
    cat_rows = ""
    for cat, price_label in [("Джейноман","1 200 ₽"),("Поклонник","5 000 ₽")]:
        tp_t = bsc.get("TimePad",{}).get(cat,{}).get("tickets",0)
        tp_r = bsc.get("TimePad",{}).get(cat,{}).get("revenue",0)
        tc_t = bsc.get("TicketsCloud",{}).get(cat,{}).get("tickets",0)
        tc_r = bsc.get("TicketsCloud",{}).get(cat,{}).get("revenue",0)
        tot_t = tp_t + tc_t; tot_r = tp_r + tc_r
        cat_rows += (f"<tr><td><strong>{cat}</strong><br>"
                     f"<small style='color:var(--mt)'>{price_label}</small></td>"
                     f"<td>{tp_t}</td><td>{tp_r:,.0f} ₽</td>"
                     f"<td>{tc_t}</td><td>{tc_r:,.0f} ₽</td>"
                     f"<td><strong>{tot_t}</strong></td>"
                     f"<td><strong>{tot_r:,.0f} ₽</strong></td></tr>")

    # Карточки прогноза
    fcj, fcp = m["fc_j"], m["fc_p"]
    pj_pct = min(fcj["sold"] / fcj["plan"] * 100, 100) if fcj["plan"] else 0
    pp_pct = min(fcp["sold"] / fcp["plan"] * 100, 100) if fcp["plan"] else 0

    def fc_card(fc, accent, price_label):
        wow = fc["wow_pct"]
        wow_txt = (f"↑ +{wow}%" if wow > 0 else (f"↓ {wow}%" if wow < 0 else "→ ровно"))
        wow_col = "var(--ok)" if wow > 0 else ("var(--warn)" if wow < 0 else "var(--mt)")
        pace_col = "var(--ok)" if fc["on_pace"] else "var(--warn)"
        pp = min(fc["sold"] / fc["plan"] * 100, 100) if fc["plan"] else 0
        fore = f'{fc["low"]}–{fc["high"]}'
        if fc["capped"]:
            fore = f'{fc["low"]}–{fc["high"]}+'
        bottom = (f'<span style="color:var(--ok)"><strong>+{fc["surplus"]}</strong> сверх плана</span>'
                  if fc["surplus"] > 0 else
                  f'<span style="color:var(--warn)"><strong>{fc["gap"]}</strong> шт не хватает</span>')
        return f"""
    <div class="fc">
      <div class="fc-head">
        <div>
          <div class="fc-title">{fc.get("name","")} · {price_label}</div>
          <div class="fc-days">Пик: {fc["peak_val"]} шт ({fc["peak_date"]})</div>
        </div>
        <span class="tag {fc["vcls"]}">{fc["verdict"]}</span>
      </div>
      <div class="fc-row"><span class="fc-label">Продано</span><span><strong>{fc["sold"]}</strong> из {fc["plan"]} · {pp:.0f}%</span></div>
      <div class="progress-bar"><div class="progress-fill" style="width:{pp:.1f}%;background:{accent}"></div></div>
      <div class="fc-row"><span class="fc-label">Текущий темп (7 дн)</span><span><strong>{fc["rate7"]}</strong> шт/день · <span style="color:{wow_col}">{wow_txt}</span></span></div>
      <div class="fc-row"><span class="fc-label">Нужный темп до плана</span><span style="color:{pace_col}"><strong>{fc["required"]}</strong> шт/день</span></div>
      <div class="fc-row"><span class="fc-label">Прогноз к 18.07 (с разгоном)</span><span><strong>{fore}</strong> шт</span></div>
      <div class="gap-row">
        <span class="fc-label">Итог к плану</span>
        {bottom}
      </div>
    </div>"""

    fcj["name"] = "Джейноман"
    fcp["name"] = "Поклонник"
    card_j = fc_card(fcj, "var(--a1)", "1 200 ₽")
    card_p = fc_card(fcp, "var(--a4)", "5 000 ₽")

    return f"""<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Джейн Фест 2026 — Продажи</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{{--bg:#0f1117;--sur:#1a1d27;--sur2:#22263a;--brd:#2e3250;
      --a1:#6c63ff;--a2:#00d4aa;--a3:#ff6b6b;--a4:#ffd93d;--a5:#f97316;
      --tx:#e8eaf6;--mt:#7b82b0;--fn:'Inter',-apple-system,sans-serif;
      --ok:#00d4aa;--warn:#ff6b6b}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--tx);font-family:var(--fn)}}
.hdr{{padding:22px 40px;border-bottom:1px solid var(--brd);display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}}
.hdr h1{{font-size:20px;font-weight:700}}.hdr p{{font-size:13px;color:var(--mt);margin-top:3px}}
.badge{{font-size:12px;color:var(--mt);background:var(--sur);padding:5px 14px;border-radius:20px;border:1px solid var(--brd);white-space:nowrap}}
.fest-badge{{background:var(--a1)22;border-color:var(--a1);color:var(--a1)}}
.flt{{padding:12px 40px;display:flex;gap:8px;border-bottom:1px solid var(--brd);flex-wrap:wrap}}
.fb{{padding:5px 14px;border-radius:20px;border:1px solid var(--brd);background:transparent;color:var(--mt);font-size:12px;cursor:pointer;transition:all .2s}}
.fb.on,.fb:hover{{background:var(--a1);color:#fff;border-color:var(--a1)}}
.fb.cancel-btn.on{{background:var(--a3);border-color:var(--a3)}}
.wrap{{padding:22px 40px;max-width:1500px}}
.section-title{{font-size:13px;font-weight:600;color:var(--mt);text-transform:uppercase;letter-spacing:.8px;margin:24px 0 12px}}

/* KPI */
.kpi{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}}
.kc{{background:var(--sur);border:1px solid var(--brd);border-radius:14px;padding:18px 20px}}
.kl{{font-size:11px;color:var(--mt);text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px}}
.kv{{font-size:28px;font-weight:700;letter-spacing:-1px}}
.kv.p{{color:var(--a1)}}.kv.g{{color:var(--a2)}}.kv.r{{color:var(--a3)}}
.ks{{font-size:12px;color:var(--mt);margin-top:4px}}

/* Прогноз */
.forecast-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:20px}}
.fc{{background:var(--sur);border:1px solid var(--brd);border-radius:14px;padding:18px 20px}}
.fc-head{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px}}
.fc-title{{font-size:14px;font-weight:600}}
.fc-days{{font-size:12px;color:var(--mt)}}
.fc-row{{display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px}}
.fc-label{{color:var(--mt)}}
.progress-bar{{width:100%;height:6px;background:var(--brd);border-radius:3px;margin:10px 0}}
.progress-fill{{height:100%;border-radius:3px;transition:width .4s}}
.tag{{font-size:11px;padding:2px 8px;border-radius:10px;font-weight:600}}
.tag.ok{{background:#00d4aa22;color:var(--ok)}}
.tag.warn{{background:#ff6b6b22;color:var(--warn)}}
.tag.mid{{background:#ffd93d22;color:var(--a4)}}
.gap-row{{margin-top:8px;padding-top:8px;border-top:1px solid var(--brd);display:flex;justify-content:space-between;font-size:13px}}

/* Charts */
.r1{{display:grid;grid-template-columns:1fr;gap:12px;margin-bottom:12px}}
.r2{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}}
.cd{{background:var(--sur);border:1px solid var(--brd);border-radius:14px;padding:20px;margin-bottom:12px}}
.ct{{font-size:14px;font-weight:600;margin-bottom:4px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}}
.seg-btns{{display:flex;gap:6px;flex-wrap:wrap}}
.seg{{padding:3px 12px;border-radius:20px;border:1px solid var(--brd);background:transparent;color:var(--mt);font-size:11px;cursor:pointer;transition:all .15s}}
.seg.on{{background:var(--a1);color:#fff;border-color:var(--a1)}}

/* Table */
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:8px 12px;font-size:10px;text-transform:uppercase;letter-spacing:.7px;color:var(--mt);border-bottom:1px solid var(--brd)}}
td{{padding:11px 12px;border-bottom:1px solid var(--brd)}}
tr:last-child td{{border-bottom:none}}tr:hover td{{background:var(--sur2)}}
.th-tp{{color:var(--a1)}}.th-tc{{color:var(--a2)}}.th-tot{{color:var(--a4)}}
.empty{{color:var(--mt);text-align:center;padding:24px}}

@media(max-width:900px){{
  .kpi,.forecast-grid,.r2{{grid-template-columns:1fr}}
  .hdr,.flt,.wrap{{padding-left:16px;padding-right:16px}}
}}
</style></head><body>

<div class="hdr">
  <div>
    <h1>🎟 Джейн Фест 2026 — Продажи билетов</h1>
    <p>TimePad + TicketsCloud · сводный дашборд</p>
  </div>
  <div style="display:flex;gap:8px;flex-wrap:wrap">
    <div class="badge fest-badge">🗓 Фестиваль: 18 июля 2026 · осталось {m['days_left']} дн.</div>
    <div class="badge">Обновлено: {m['updated_at']}</div>
  </div>
</div>

<div class="flt">
  <span style="font-size:12px;color:var(--mt);align-self:center;margin-right:4px">Период:</span>
  <button class="fb on" onclick="filt(0,this)">Всё время</button>
  <button class="fb" onclick="filt(7,this)">7 дней</button>
  <button class="fb" onclick="filt(14,this)">14 дней</button>
  <button class="fb" onclick="filt(30,this)">30 дней</button>
  <span style="flex:1"></span>
  <button class="fb cancel-btn" id="btnCancel" onclick="toggleCancel(this)">+ Отменённые</button>
</div>

<div class="wrap">

  <!-- KPI -->
  <div class="section-title">Ключевые показатели</div>
  <div class="kpi">
    <div class="kc"><div class="kl">Билетов продано</div><div class="kv p" id="kt">{m['total_tickets']}</div><div class="ks">за выбранный период</div></div>
    <div class="kc"><div class="kl">Выручка</div><div class="kv g" id="kr">{m['total_revenue']:,.0f} ₽</div><div class="ks">оплаченные заказы</div></div>
    <div class="kc"><div class="kl">Средний чек</div><div class="kv" id="ka">{m['avg_check']:,.0f} ₽</div><div class="ks">на один билет</div></div>
    <div class="kc"><div class="kl">До фестиваля</div><div class="kv r">{m['days_left']}</div><div class="ks">дней осталось</div></div>
  </div>

  <!-- Прогноз -->
  <div class="section-title">Прогноз к 18 июля · с учётом разгона продаж в финале</div>
  <div class="forecast-grid">
    {card_j}
    {card_p}
  </div>
  <div style="font-size:11px;color:var(--mt);margin:-8px 0 20px;line-height:1.5">
    Прогноз = текущий темп (среднее за 14 дней) × оставшиеся дни, где финальные недели взвешены тяжелее:
    продажи событий обычно резко ускоряются в последние 4–6 недель (до ~50% билетов уходит в последние 2 недели).
    «Нужный темп» — сколько билетов в день надо продавать, чтобы выйти на план. Диапазон: нижняя граница — без ускорения, верхняя — с типичным финальным всплеском.
  </div>

  <!-- Главный график с селектором -->
  <div class="cd">
    <div class="ct">
      <span>Динамика продаж по дням</span>
      <div class="seg-btns">
        <button class="seg on" onclick="setMode('src',this)">По площадкам</button>
        <button class="seg" onclick="setMode('type',this)">По типам билетов</button>
        <button class="seg" onclick="setMode('rev',this)">Выручка ₽</button>
      </div>
    </div>
    <div style="font-size:12px;color:var(--mt);margin-bottom:14px" id="chartSubtitle">Количество билетов · TimePad vs TicketsCloud</div>
    <canvas id="cMain" height="100"></canvas>
  </div>

  <!-- Дополнительные графики -->
  <div class="r2">
    <div class="cd"><div class="ct">Доля площадок</div><canvas id="cS" height="160"></canvas></div>
    <div class="cd"><div class="ct">Типы билетов — итого</div><canvas id="cCat" height="160"></canvas></div>
  </div>

  <!-- Таблица типов по площадкам -->
  <div class="cd">
    <div class="ct">Продажи по типам билетов и площадкам</div>
    <table>
      <thead>
        <tr>
          <th>Тип билета</th>
          <th class="th-tp" colspan="2">TimePad</th>
          <th class="th-tc" colspan="2">TicketsCloud</th>
          <th class="th-tot" colspan="2">Итого</th>
        </tr>
        <tr>
          <th></th>
          <th>Билетов</th><th>Выручка</th>
          <th>Билетов</th><th>Выручка</th>
          <th>Билетов</th><th>Выручка</th>
        </tr>
      </thead>
      <tbody>{cat_rows}</tbody>
    </table>
  </div>

</div>

<script>
// ── Данные ──────────────────────────────────────────────
const AD={json.dumps(days)};
const tpT={json.dumps(tpT)},tcT={json.dumps(tcT)};
const tpR={json.dumps(tpR)},tcR={json.dumps(tcR)};
const jT={json.dumps(jT)},pT={json.dumps(pT)};
const jR={json.dumps(jR)},pR={json.dumps(pR)};
const jC={json.dumps(jC)},pC={json.dumps(pC)};
const allC={json.dumps(allC)};
const sL={json.dumps(sL)},sT={json.dumps(sT)};

// ── Chart.js defaults ───────────────────────────────────
Chart.defaults.color='#7b82b0';
Chart.defaults.font.family="'Inter',sans-serif";
Chart.defaults.font.size=12;
const gr='rgba(255,255,255,0.06)';
function grd(c,col){{const g=c.createLinearGradient(0,0,0,220);g.addColorStop(0,col+'55');g.addColorStop(1,col+'00');return g;}}

// ── Пончик: площадки ────────────────────────────────────
new Chart(document.getElementById('cS'),{{
  type:'doughnut',
  data:{{labels:sL,datasets:[{{data:sT,backgroundColor:['#6c63ff','#00d4aa'],borderWidth:0,hoverOffset:8}}]}},
  options:{{responsive:true,cutout:'65%',plugins:{{legend:{{position:'bottom'}}}}}}
}});

// ── Горизонтальный бар: типы ────────────────────────────
    const catTot = [{cat_j_t},{cat_p_t}];
new Chart(document.getElementById('cCat'),{{
  type:'bar',
  data:{{
    labels:['Джейноман','Поклонник'],
    datasets:[{{label:'Билеты',data:catTot,backgroundColor:['#6c63ff','#ffd93d'],borderRadius:6}}]
  }},
  options:{{
    indexAxis:'y',responsive:true,
    plugins:{{legend:{{display:false}}}},
    scales:{{x:{{grid:{{color:gr}},beginAtZero:true}},y:{{grid:{{color:'transparent'}}}}}}
  }}
}});

// ── Главный график ───────────────────────────────────────
const xM = document.getElementById('cMain').getContext('2d');
let showCancel = false;
let currentMode = 'src';

function buildDatasets(mode, cancel) {{
  const base = [];
  if (mode === 'src') {{
    base.push({{label:'TimePad',data:tpT,backgroundColor:'#6c63ffcc',borderRadius:4,stack:'s'}});
    base.push({{label:'TicketsCloud',data:tcT,backgroundColor:'#00d4aacc',borderRadius:4,stack:'s'}});
  }} else if (mode === 'type') {{
    base.push({{label:'Джейноман',data:jT,backgroundColor:'#6c63ffcc',borderRadius:4,stack:'s'}});
    base.push({{label:'Поклонник',data:pT,backgroundColor:'#ffd93dcc',borderRadius:4,stack:'s'}});
  }} else {{
    base.push({{label:'TimePad ₽',data:tpR,borderColor:'#6c63ff',backgroundColor:grd(xM,'#6c63ff'),type:'line',tension:.4,fill:true,pointRadius:3,yAxisID:'y'}});
    base.push({{label:'TicketsCloud ₽',data:tcR,borderColor:'#00d4aa',backgroundColor:grd(xM,'#00d4aa'),type:'line',tension:.4,fill:true,pointRadius:3,yAxisID:'y'}});
  }}
  if (cancel) {{
    base.push({{label:'Отменено',data:allC,backgroundColor:'#ff6b6b66',borderRadius:4,stack:'c'}});
  }}
  return base;
}}

const subtitles = {{
  src: 'Количество билетов · TimePad vs TicketsCloud',
  type: 'Количество билетов · Джейноман vs Поклонник',
  rev: 'Выручка в рублях · TimePad vs TicketsCloud'
}};

const chMain = new Chart(xM, {{
  type: 'bar',
  data: {{labels: AD, datasets: buildDatasets('src', false)}},
  options: {{
    responsive: true,
    plugins: {{legend: {{position: 'top'}}}},
    scales: {{
      x: {{stacked: true, grid: {{color: gr}}, ticks: {{maxRotation: 45}}}},
      y: {{stacked: true, grid: {{color: gr}}, beginAtZero: true,
           ticks: {{callback: v => currentMode==='rev' ? v.toLocaleString('ru-RU')+' ₽' : v}}}}
    }}
  }}
}});

function setMode(mode, btn) {{
  document.querySelectorAll('.seg').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  currentMode = mode;
  document.getElementById('chartSubtitle').textContent = subtitles[mode];
  chMain.data.datasets = buildDatasets(mode, showCancel);
  chMain.options.scales.y.stacked = mode !== 'rev';
  chMain.options.scales.x.stacked = mode !== 'rev';
  // Для режима выручки меняем тип на line
  chMain.data.datasets.forEach(ds => {{
    if (!ds.type) ds.type = mode === 'rev' ? 'line' : 'bar';
  }});
  chMain.update();
}}

function toggleCancel(btn) {{
  showCancel = !showCancel;
  btn.classList.toggle('on', showCancel);
  chMain.data.datasets = buildDatasets(currentMode, showCancel);
  chMain.update();
}}

// ── Фильтр по периоду ────────────────────────────────────
function filt(n, btn) {{
  document.querySelectorAll('.flt .fb:not(.cancel-btn)').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  let fd = AD;
  if (n > 0) {{
    const cut = new Date(); cut.setDate(cut.getDate() - n);
    const cs  = cut.toISOString().slice(0,10);
    fd = AD.filter(d => d >= cs);
  }}
  const idx = AD.map((d,i) => fd.includes(d) ? i : -1).filter(i => i >= 0);
  const sl  = a => idx.map(i => a[i]);

  chMain.data.labels = sl(AD);
  chMain.data.datasets = buildDatasets(currentMode, showCancel).map((ds,i) => {{
    const arrs = [tpT,tcT,jT,pT,tpR,tcR,allC];
    // Подбираем правильный массив по метке
    const map = {{'TimePad':tpT,'TicketsCloud':tcT,'Джейноман':jT,'Поклонник':pT,
                  'TimePad ₽':tpR,'TicketsCloud ₽':tcR,'Отменено':allC}};
    ds.data = sl(map[ds.label] || []);
    return ds;
  }});
  chMain.update();

  const tt = idx.reduce((s,i) => s+(tpT[i]||0)+(tcT[i]||0), 0);
  const tr = idx.reduce((s,i) => s+(tpR[i]||0)+(tcR[i]||0), 0);
  document.getElementById('kt').textContent = tt;
  document.getElementById('kr').textContent = tr.toLocaleString('ru-RU') + ' ₽';
  document.getElementById('ka').textContent = tt ? Math.round(tr/tt).toLocaleString('ru-RU')+' ₽' : '0 ₽';
}}
</script></body></html>"""


# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════

def main():
    print("="*55)
    print("  Дашборд продаж — Джейн Фест 2026")
    print("="*55)
    all_sales = []

    # ── TimePad ──────────────────────────────────────────
    print(f"\n[1/4] TimePad — мероприятие {TIMEPAD_EVENT_ID}...")
    tp_event = get_timepad_event()
    if tp_event:
        print(f"      Мероприятие: «{tp_event.get('name','?')}»")
        print(f"[2/4] TimePad — загружаем заказы...")
        tp_orders = get_timepad_orders()
        print(f"      Заказов: {len(tp_orders)}")
        tp_sales  = parse_timepad(tp_event, tp_orders)
        all_sales.extend(tp_sales)
        paid   = sum(1 for s in tp_sales if not s["cancelled"])
        cancel = sum(1 for s in tp_sales if s["cancelled"])
        print(f"      Билетов оплачено: {paid}, отменено: {cancel}")
    else:
        print("      Не найдено, пропускаем.")

    # ── TicketsCloud ─────────────────────────────────────
    print(f"\n[3/4] TicketsCloud — мероприятие {TICKETSCLOUD_EVENT_ID}...")
    tc_event  = get_tc_event()
    if tc_event:
        name = tc_event.get("name", {})
        ename = (name.get("ru") or name.get("en") or "?") if isinstance(name, dict) else str(name)
        print(f"      Мероприятие: «{ename}»")
    tc_orders = get_tc_orders()
    print(f"      Заказов: {len(tc_orders)}")
    tc_sales  = parse_tc(tc_event, tc_orders)
    all_sales.extend(tc_sales)
    paid   = sum(1 for s in tc_sales if not s["cancelled"])
    cancel = sum(1 for s in tc_sales if s["cancelled"])
    print(f"      Билетов оплачено: {paid}, отменено: {cancel}")

    # ── Итог ─────────────────────────────────────────────
    total_paid = sum(1 for s in all_sales if not s["cancelled"])
    print(f"\n[4/4] Генерация дашборда (оплачено: {total_paid} билетов)...")

    metrics = aggregate(all_sales)

    # Показываем разбивку по типам
    fj, fp = metrics["fc_j"], metrics["fc_p"]
    print(f"      Джейноман: {fj['sold']} шт · темп {fj['rate7']}/день · нужно {fj['required']}/день · прогноз {fj['low']}-{fj['high']} (план {PLAN_J})")
    print(f"      Поклонник: {fp['sold']} шт · темп {fp['rate7']}/день · нужно {fp['required']}/день · прогноз {fp['low']}-{fp['high']} (план {PLAN_P})")

    out_dir = os.path.dirname(OUTPUT_FILE)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(generate_html(metrics))

    print(f"\n✅ Дашборд готов!")
    print(f"   Файл: {OUTPUT_FILE}")
    print(f'   Открыть: open "{OUTPUT_FILE}"')
    print("="*55)

if __name__ == "__main__":
    main()
