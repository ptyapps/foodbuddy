#!/usr/bin/env python3
"""XopaFit price collector for VTEX supermarket storefronts (Costa Rica, Colombia, ...).

For each ingredient the app prices, it searches each store's public catalog (the same data the store's website
shows), reads the package size from the product name, converts to the app's basis (per lb for weighed items,
per quart for liquids, per piece/slice otherwise) in local currency, and writes prices-<MARKET>[-<REGION>].json.
The value per ingredient is the median across the matching products of the region's chains (robust to outliers).

Polite by design: one search per ingredient per store, a pause between requests, a clear User-Agent.
Usage: python3 tools/collect.py            (writes all markets into the repo root)
"""
import json, re, statistics, sys, time, urllib.parse, urllib.request, datetime, os

UA = "XopaFit price check (+https://xopafit.app/support/)"
PAUSE = 0.6

STORES = {
    "masxmenos": "www.masxmenos.cr",
    "olimpica": "www.olimpica.com",
    "jumbo": "www.jumbocolombia.com",
    "carulla": "www.carulla.com/io",
    "d1": "www.d1.com.co",
}
MARKETS = {
    "CR":      {"currency": "CRC", "fx": 505,  "stores": ["masxmenos"]},
    "CO-BOG":  {"currency": "COP", "fx": 4000, "stores": ["carulla", "jumbo", "d1"]},
    "CO-ANT":  {"currency": "COP", "fx": 4000, "stores": ["carulla", "jumbo", "d1"]},
    "CO-VAC":  {"currency": "COP", "fx": 4000, "stores": ["jumbo", "olimpica", "d1"]},
    "CO-ATL":  {"currency": "COP", "fx": 4000, "stores": ["olimpica", "d1"]},
}

# ingredient: (search term, basis, required words, excluded words, grams per piece for "pc" items sold by weight)
# basis: "lb" (weighed), "qt" (liquid), "pc" (piece), "slice" (bread slice, ~30 g), "egg"
ITEMS = {
    "Apple": ("manzana", "pc", ["manzana"], ["jugo", "compota", "vinagre", "té", "galleta", "sidra"], 180),
    "Avocado": ("aguacate", "pc", ["aguacate"], ["salsa", "guacamole", "aceite"], 250),
    "Banana": ("banano", "pc", ["banano"], ["chips", "harina", "deshidratado", "verde"], 180),
    "Black beans, low-sodium": ("frijoles negros", "lb", ["negro"], ["refrito", "molido", "sopa"], 0),
    "Broccoli": ("brocoli", "lb", ["br"], ["sopa", "crema"], 0),
    "Brown rice, dry": ("arroz integral", "lb", ["integral"], ["galleta", "tortita", "bebida", "harina"], 0),
    "Cauliflower": ("coliflor", "lb", ["coliflor"], ["arroz de", "sopa"], 0),
    "Cheddar cheese": ("queso cheddar", "lb", ["cheddar"], ["salsa", "galleta", "snack", "papas", "dip"], 0),
    "Chicken breast": ("pechuga de pollo", "lb", ["pechuga"], ["apanad", "nugget", "jamón", "salchicha", "embutido", "pavo"], 0),
    "Chicken thighs, skinless": ("muslo de pollo", "lb", ["muslo"], ["apanad", "pavo"], 0),
    "Chicken thighs, bone-in, skin off": ("muslo de pollo", "lb", ["muslo"], ["apanad", "pavo"], 0),
    "Chickpeas, low-sodium": ("garbanzos", "lb", ["garbanzo"], ["harina", "hummus", "snack"], 0),
    "Corn on the cob": ("mazorca", "pc", ["mazorca"], ["enlatad", "lata", "crema"], 250),
    "Cucumber": ("pepino", "pc", ["pepino"], ["encurtid", "aderezo"], 300),
    "Eggs": ("huevos", "egg", ["huevo"], ["codorniz", "chocolate", "pascua", "clara", "líquido"], 0),
    "Fresh garlic": ("ajo", "lb", ["ajo"], ["sal", "polvo", "pasta", "salsa", "aceite", "aderezo", "pan "], 0),
    "Grapes": ("uvas", "lb", ["uva"], ["pasas", "jugo", "vino", "gelatina", "mermelada"], 0),
    "Greek yogurt, whole": ("yogurt griego", "lb", ["griego"], [], 0),
    "Ground coffee": ("cafe molido", "lb", ["caf"], ["instant", "cápsula", "capsula", "soluble", "grano"], 0),
    "Kidney beans, low-sodium": ("frijoles rojos", "lb", ["rojo"], ["refrito", "sopa"], 0),
    "Lean beef stew meat": ("carne de res para guisar", "lb", ["res"], ["molida", "hamburguesa", "salchicha"], 0),
    "Lean ground beef 93/7": ("carne molida de res", "lb", ["molida"], ["cerdo", "pollo", "pavo", "mixta", "hamburguesa"], 0),
    "Lentils, dry": ("lentejas", "lb", ["lenteja"], ["sopa", "snack", "lata"], 0),
    "Light coconut milk": ("leche de coco", "qt", ["coco"], ["agua de coco", "crema", "polvo", "galleta"], 0),
    "Light tuna, canned in water": ("atun en agua", "lb", ["at"], ["aceite", "galleta", "ensalada"], 0),
    "Limes": ("limon", "pc", ["lim"], ["jugo", "té", "bebida", "galleta", "sal", "pie", "helado", "agua"], 60),
    "Low-sodium soy sauce": ("salsa de soya", "qt", ["soya"], ["leche", "bebida", "proteína"], 0),
    "Mayonnaise": ("mayonesa", "lb", ["mayonesa"], [], 0),
    "Mozzarella": ("queso mozzarella", "lb", ["mozzarella"], ["palito", "snack", "pizza"], 0),
    "Olive oil": ("aceite de oliva", "qt", ["oliva"], ["spray", "aceituna"], 0),
    "Olives": ("aceitunas", "lb", ["aceituna"], ["aceite"], 0),
    "Papaya": ("papaya", "lb", ["papaya"], ["jugo", "deshidratada", "mermelada"], 0),
    "Parmesan": ("queso parmesano", "lb", ["parmesan"], ["galleta", "salsa"], 0),
    "Pasta, dry": ("spaghetti", "lb", ["spag"], ["salsa", "lata"], 0),
    "Peanut butter, no sugar added": ("mantequilla de mani", "lb", ["man"], ["galleta", "chocolate", "barra", "snack"], 0),
    "Pear": ("pera", "pc", ["pera"], ["jugo", "compota", "néctar", "nectar", "pera en almíbar", "almibar"], 180),
    "Plain rice cakes": ("tortitas de arroz", "lb", ["arroz"], ["chocolate", "yogurt"], 0),
    "Pork chops, boneless": ("chuleta de cerdo", "lb", ["chuleta"], ["ahumad", "apanad"], 0),
    "Pork tenderloin": ("lomo de cerdo", "lb", ["lomo"], ["ahumad", "jamón", "embutido", "res"], 0),
    "Potatoes": ("papa", "lb", ["papa"], ["frita", "chips", "pure", "puré", "congelad", "papaya", "papas fritas", "snack", "harina"], 0),
    "Queso blanco": ("queso blanco", "lb", ["queso"], ["crema", "untable", "rallado", "palito"], 0),
    "Ripe plantain": ("platano maduro", "pc", ["pl"], ["chips", "harina", "frito", "tajada", "patacón", "patacon"], 300),
    "Rolled oats": ("avena en hojuelas", "lb", ["avena"], ["bebida", "galleta", "barra", "leche"], 0),
    "Salmon fillet": ("salmon", "lb", ["salm"], ["ahumad", "lata", "pasta", "comida para"], 0),
    "Sirloin steak, trimmed": ("lomo de res", "lb", ["res"], ["molida", "salchicha", "hamburguesa"], 0),
    "Sour cream": ("crema agria", "lb", ["agria"], [], 0),
    "Spinach": ("espinaca", "lb", ["espinaca"], ["pasta", "tortilla", "crema"], 0),
    "Strawberries, frozen": ("fresas congeladas", "lb", ["fres"], ["yogurt", "mermelada", "helado", "jugo", "galleta"], 0),
    "Tomato sauce, no salt added": ("salsa de tomate", "lb", ["tomate"], ["ketchup", "kétchup"], 0),
    "Unsalted almonds": ("almendras", "lb", ["almendra"], ["leche", "bebida", "chocolate", "crema"], 0),
    "Unsalted cashews": ("marañon", "lb", ["mara"], ["chocolate"], 0),
    "Unsalted mixed nuts": ("mezcla de nueces", "lb", ["nuez", "nueces", "mix", "mezcla"], ["chocolate", "galleta"], 0),
    "Walnuts": ("nueces", "lb", ["nuez", "nueces"], ["mezcla", "mix", "chocolate", "galleta", "moscada", "macadamia", "brasil"], 0),
    "Watermelon": ("sandia", "lb", ["sand"], ["jugo"], 0),
    "Whey protein": ("proteina whey", "lb", ["whey"], ["barra", "galleta"], 0),
    "Whole milk": ("leche entera", "qt", ["leche"], ["polvo", "condensada", "evaporada", "deslactosada", "chocolate", "fresa", "coco", "almendra", "soya", "avena"], 0),
    "Whole-grain bread": ("pan integral", "slice", ["integral"], ["galleta", "tostada", "harina", "molde de"], 0),
    "Whole-wheat tortilla, large": ("tortilla integral", "pc", ["tortilla"], ["chips", "maíz", "maiz", "nachos"], 0),
    "Yuca": ("yuca", "lb", ["yuca"], ["frita", "chips", "harina", "almidón", "congelad"], 0),
    "Zucchini": ("zucchini", "lb", ["zucchini", "calabac"], [], 0),
    "Bacon": ("tocineta", "lb", ["tocin", "bacon"], ["sabor", "bits", "papas"], 0),
    "Berries": ("frutos rojos", "lb", ["frut", "mora", "arándano", "arandano"], ["yogurt", "mermelada", "jugo", "té", "helado"], 0),
    "Bok choy": ("pak choi", "lb", ["pak", "bok"], [], 0),
    "Cabbage": ("repollo", "lb", ["repollo"], ["ensalada"], 0),
    "Carrots": ("zanahoria", "lb", ["zanahoria"], ["jugo", "torta", "baby food", "compota"], 0),
    "Corn tortillas": ("tortilla de maiz", "pc", ["tortilla"], ["chips", "nachos", "harina de trigo", "integral"], 0),
    "Corvina fillet": ("corvina", "lb", ["corvina"], [], 0),
    "Couscous, dry": ("cuscus", "lb", ["cus"], [], 0),
    "Crushed tomatoes, no salt added": ("tomate triturado", "lb", ["tomate"], ["ketchup", "salsa de tomate"], 0),
    "Curry powder": ("curry", "lb", ["curry"], ["salsa", "pasta"], 0),
    "Eggplant": ("berenjena", "lb", ["berenjena"], [], 0),
    "Feta cheese": ("queso feta", "lb", ["feta"], [], 0),
    "Firm tofu": ("tofu", "lb", ["tofu"], [], 0),
    "Fresh culantro": ("cilantro", "lb", ["cilantro", "culantro"], ["salsa", "aderezo", "seco"], 0),
    "Fresh ginger": ("jengibre", "lb", ["jengibre"], ["té", "galleta", "bebida", "polvo", "molido"], 0),
    "Green beans": ("habichuela", "lb", ["habichuela", "vainica", "ejote"], ["lata"], 0),
    "Green onions": ("cebollin", "lb", ["cebol"], ["polvo", "deshidrat"], 0),
    "Green plantain": ("platano verde", "pc", ["verde"], ["chips", "harina", "patacón", "patacon"], 300),
    "Ground turkey": ("carne molida de pavo", "lb", ["pavo"], ["jamón", "salchicha", "pechuga"], 0),
    "Mango": ("mango", "pc", ["mango"], ["jugo", "néctar", "nectar", "mermelada", "deshidratado", "helado", "té"], 350),
    "Mushrooms": ("champiñones", "lb", ["champi", "hongo"], ["crema", "sopa", "salsa", "lata"], 0),
    "No-sugar-added jelly": ("mermelada sin azucar", "lb", ["mermelada"], [], 0),
    "Onion": ("cebolla", "lb", ["cebolla"], ["polvo", "aros", "frita", "encurtida", "sopa", "deshidrat"], 0),
    "Otoe": ("tiquisque", "lb", ["tiquisque", "ñame", "malanga", "otoe"], ["harina", "chips"], 0),
    "Panko breadcrumbs": ("panko", "lb", ["panko", "apanar"], [], 0),
    "Peas, frozen": ("arvejas congeladas", "lb", ["arveja", "guisante"], ["lata"], 0),
    "Pesto": ("pesto", "lb", ["pesto"], [], 0),
    "Pineapple": ("piña", "lb", ["piña"], ["jugo", "lata", "almíbar", "almibar", "mermelada", "néctar"], 0),
    "Precooked corn flour": ("harina de maiz precocida", "lb", ["harina"], ["trigo"], 0),
    "Quinoa, dry": ("quinua", "lb", ["quin"], ["barra", "galleta", "bebida"], 0),
    "Rice noodles, dry": ("fideos de arroz", "lb", ["arroz"], ["galleta", "tortita"], 0),
    "Sesame oil": ("aceite de ajonjoli", "qt", ["ajonjol", "sésamo", "sesamo"], [], 0),
    "Shrimp, raw": ("camaron", "lb", ["camar"], ["sopa", "galleta", "snack", "salsa"], 0),
    "Sweet potato": ("camote", "lb", ["camote", "batata", "boniato"], ["chips", "frita"], 0),
    "Tilapia fillet": ("tilapia", "lb", ["tilapia"], [], 0),
    "Tomatoes": ("tomate", "lb", ["tomate"], ["salsa", "pasta", "triturado", "lata", "ketchup", "jugo", "seco", "cherry", "pure", "puré"], 0),
    "Unsalted pumpkin seeds": ("semillas de calabaza", "lb", ["calabaza", "auyama", "ahuyama", "pepita"], [], 0),
    "Unsweetened soy milk": ("bebida de soya", "qt", ["soya"], ["salsa", "proteína"], 0),
    "Whole-grain crackers": ("galletas integrales", "lb", ["integral"], ["chocolate", "rellena"], 0),
    "Zapallo": ("ahuyama", "lb", ["ahuyama", "auyama", "ayote", "zapallo", "calabaza"], ["semilla", "crema", "sopa", "pepita"], 0),
}

LB = 453.592
QT_ML = 946.353
SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|kilo|kilos|gr|grs|g|lb|lbs|libra|libras|oz|ml|cc|lt|lts|litro|litros|l)\b", re.I)
COUNT = re.compile(r"(?:x\s*|\b)(\d{1,3})\s*(?:und|unds|unidades|unid|uds|u|pz|piezas|huevos)\b", re.I)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def search(host, term):
    q = urllib.parse.quote(term)
    try:
        return get(f"https://{host}/api/catalog_system/pub/products/search?ft={q}&_from=0&_to=24")
    except Exception as e:
        print(f"   ! {host} '{term}': {type(e).__name__}", file=sys.stderr)
        return []


def grams_or_ml(name):
    """Total size in g (or ml) from a product name, or None. Handles '2 x 500 g' and 'x kg'."""
    n = name.lower().replace(",", ".")
    if re.search(r"\b(x|por|el|1)?\s*kg\b", n) and not SIZE.search(n):
        return 1000.0, "g"
    m = SIZE.search(n)
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2).lower()
    mult = re.search(r"(\d+)\s*x\s*\d", n)
    k = float(mult.group(1)) if mult else 1.0
    if u in ("kg", "kilo", "kilos"): return v * 1000 * k, "g"
    if u in ("g", "gr", "grs"): return v * k, "g"
    if u in ("lb", "lbs", "libra", "libras"): return v * LB * k, "g"
    if u == "oz": return v * 28.35 * k, "g"
    if u in ("ml", "cc"): return v * k, "ml"
    if u in ("l", "lt", "lts", "litro", "litros"): return v * 1000 * k, "ml"
    return None


def unit_price(name, price, basis, piece_g):
    """Local price in the app's basis, or None when the size can't be read."""
    n = name.lower()
    if basis == "egg":
        c = COUNT.search(n) or re.search(r"x\s*(\d{1,3})\b", n)
        return price / float(c.group(1)) if c and 6 <= int(c.group(1)) <= 90 else None
    if basis == "pc":
        c = COUNT.search(n)
        if c and 1 <= int(c.group(1)) <= 60 and not SIZE.search(n):
            return price / float(c.group(1))
        s = grams_or_ml(n)
        if s and piece_g:
            return price / s[0] * piece_g
        if re.search(r"\b(und|unidad|c/u)\b", n):
            return price
        return None
    s = grams_or_ml(n)
    if not s:
        return None
    total, kind = s
    if basis == "lb":
        return price / total * LB if kind == "g" else price / total * LB  # liquids sold by ml ≈ g
    if basis == "qt":
        return price / total * QT_ML
    if basis == "slice":
        return price / max(1.0, total / 30.0) if kind == "g" else None
    return None


def matches(name, req, exc):
    n = name.lower()
    return any(r in n for r in req) and not any(e in n for e in exc)


def collect_store(host, have=None):
    out = dict(have or {})
    for ing, (term, basis, req, exc, pg) in ITEMS.items():
        if ing in out:
            continue
        vals = []
        for p in search(host, term):
            try:
                name = p["productName"]
                offer = p["items"][0]["sellers"][0]["commertialOffer"]
                price = float(offer.get("Price") or 0)
                if price <= 0 or not offer.get("IsAvailable", True) or not matches(name, req, exc):
                    continue
                v = unit_price(name, price, basis, pg)
                if v and v > 0:
                    vals.append((v, name))
            except Exception:
                continue
        if vals:
            vals.sort()
            # Drop the extreme ends (bulk sacks, gourmet imports) when there are enough products.
            core = vals[len(vals) // 5: len(vals) - len(vals) // 5] if len(vals) >= 5 else vals
            out[ing] = (statistics.median(v for v, _ in core), core[len(core) // 2][1], len(vals))
        time.sleep(PAUSE)
    return out


# Sanity range against Panama's reference prices (converted with fx). Outside it, the item is left out and the
# app uses its own estimate × fx instead: a misread package size must never become a price.
LOW, HIGH = 0.4, 2.8


def main(root):
    cache = os.path.join(os.path.dirname(__file__), ".cache.json")
    per_store = json.load(open(cache)) if os.path.exists(cache) and "--fresh" not in sys.argv else {}
    for key, host in STORES.items():
        print(f"== {key} ({host})", file=sys.stderr)
        per_store[key] = collect_store(host, per_store.get(key))
        print(f"   {len(per_store[key])} of {len(ITEMS)} items", file=sys.stderr)
    json.dump(per_store, open(cache, "w"), ensure_ascii=False)
    # Reference: Panama's live list, then the app's built-in estimates (USD, same basis).
    panama = json.load(open(os.path.join(os.path.dirname(__file__), "reference-usd.json")))
    panama.update(json.load(open(os.path.join(root, "prices.json")))["prices"])
    today = datetime.date.today().isoformat()
    for mk, cfg in MARKETS.items():
        prices, products = {}, {}
        for ing in ITEMS:
            vals = [(per_store[s][ing][0], s, per_store[s][ing][1]) for s in cfg["stores"] if ing in per_store[s]]
            if not vals:
                continue
            ref = panama.get(ing, 0) * cfg["fx"]
            vals = [x for x in vals if not ref or LOW <= x[0] / ref <= HIGH]
            if not vals:
                continue
            med = statistics.median(v for v, _, _ in vals)
            decimals = 0 if cfg["currency"] in ("COP", "CRC") else 2
            prices[ing] = round(med, decimals) if decimals else int(round(med))
            products[ing] = "; ".join(f"{n} ({s})" for _, s, n in vals)
        payload = {
            "updated": today, "market": mk, "currency": cfg["currency"], "fx": cfg["fx"],
            "basis": "per lb for weighed items, per quart for liquids, per piece or slice otherwise",
            "note": "Approximate prices from public supermarket listings (local price references). Items not listed fall back to the app's estimate converted with fx.",
            "prices": prices, "products": products,
        }
        path = os.path.join(root, f"prices-{mk}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print(f"wrote {path}: {len(prices)} prices", file=sys.stderr)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else os.path.join(os.path.dirname(__file__), ".."))
