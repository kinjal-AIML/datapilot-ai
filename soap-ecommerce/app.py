"""
Luxe Lather — Luxury Handmade Soap E-Commerce
Flask backend with product catalog, cart, and AI recommendation engine.
"""

from flask import Flask, render_template, request, jsonify, session
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "luxe-lather-dev-key-change-in-prod")

# ---------------------------------------------------------------------------
# Product Catalog
# ---------------------------------------------------------------------------

PRODUCTS = [
    {
        "id": 1,
        "name": "Lavender Dreams",
        "tagline": "Calm your senses",
        "price": 14.99,
        "image": "/static/images/lavender.svg",
        "gallery": ["/static/images/lavender.svg", "/static/images/lavender-alt.svg"],
        "category": "Floral",
        "scent": "lavender",
        "skin_types": ["dry", "sensitive"],
        "ingredients": [
            "Saponified Olive Oil", "Coconut Oil", "Shea Butter",
            "Lavender Essential Oil", "Dried Lavender Buds",
            "Vitamin E", "Kaolin Clay"
        ],
        "benefits": [
            "Deeply moisturizes dry skin",
            "Calms irritation and redness",
            "Promotes relaxation through aromatherapy",
            "Gentle enough for sensitive skin"
        ],
        "description": (
            "Handcrafted with organic lavender essential oil and dried lavender buds, "
            "this bar soothes the skin while enveloping you in a calming floral fragrance. "
            "Enriched with shea butter and olive oil for deep hydration."
        ),
        "weight": "120g",
        "rating": 4.8,
        "reviews": [
            {"author": "Sophia M.", "rating": 5, "text": "The most luxurious soap I have ever used. My skin feels incredibly soft.", "date": "2025-12-10"},
            {"author": "James R.", "rating": 5, "text": "Beautiful scent that lingers all day. Perfectly moisturizing.", "date": "2025-11-28"},
            {"author": "Anika P.", "rating": 4, "text": "Lovely bar, lathers beautifully. Would love a larger size.", "date": "2025-11-15"},
        ],
        "featured": True,
    },
    {
        "id": 2,
        "name": "Charcoal Detox",
        "tagline": "Deep cleanse, pure skin",
        "price": 16.99,
        "image": "/static/images/charcoal.svg",
        "gallery": ["/static/images/charcoal.svg", "/static/images/charcoal-alt.svg"],
        "category": "Detox",
        "scent": "eucalyptus",
        "skin_types": ["oily"],
        "ingredients": [
            "Activated Bamboo Charcoal", "Coconut Oil", "Tea Tree Oil",
            "Eucalyptus Essential Oil", "Bentonite Clay",
            "Jojoba Oil", "Vitamin E"
        ],
        "benefits": [
            "Draws out impurities and toxins",
            "Controls excess oil production",
            "Minimizes the appearance of pores",
            "Leaves skin feeling refreshed and clean"
        ],
        "description": (
            "Powered by activated bamboo charcoal and bentonite clay, this detoxifying bar "
            "draws out impurities while tea tree and eucalyptus oils purify and refresh. "
            "Ideal for oily and combination skin types."
        ),
        "weight": "110g",
        "rating": 4.7,
        "reviews": [
            {"author": "Liam K.", "rating": 5, "text": "Finally a soap that controls my oily skin without drying it out.", "date": "2025-12-05"},
            {"author": "Priya S.", "rating": 5, "text": "The charcoal really works. My pores look so much smaller.", "date": "2025-11-20"},
            {"author": "Marcus T.", "rating": 4, "text": "Great deep clean. The eucalyptus scent is invigorating.", "date": "2025-10-30"},
        ],
        "featured": True,
    },
    {
        "id": 3,
        "name": "Rose Petal Bliss",
        "tagline": "Timeless elegance",
        "price": 18.99,
        "image": "/static/images/rose.svg",
        "gallery": ["/static/images/rose.svg", "/static/images/rose-alt.svg"],
        "category": "Floral",
        "scent": "rose",
        "skin_types": ["dry", "sensitive"],
        "ingredients": [
            "Rose Hip Seed Oil", "Goat Milk", "Shea Butter",
            "Rose Absolute", "Dried Rose Petals",
            "Glycerin", "Pink Himalayan Salt"
        ],
        "benefits": [
            "Intensely nourishes and hydrates",
            "Evens out skin tone naturally",
            "Anti-aging properties from rosehip oil",
            "Gentle exfoliation from rose petals"
        ],
        "description": (
            "A luxurious blend of rosehip seed oil, goat milk, and real rose petals. "
            "This artisan bar provides intense hydration and gentle exfoliation, "
            "leaving your skin radiant and silky smooth."
        ),
        "weight": "125g",
        "rating": 4.9,
        "reviews": [
            {"author": "Elena V.", "rating": 5, "text": "Absolutely divine. The rose scent is authentic and not overwhelming.", "date": "2025-12-15"},
            {"author": "Charlotte B.", "rating": 5, "text": "My skin has never felt so soft. This is my holy grail soap.", "date": "2025-12-01"},
            {"author": "David L.", "rating": 5, "text": "Bought as a gift and ended up keeping it. Pure luxury.", "date": "2025-11-18"},
        ],
        "featured": True,
    },
    {
        "id": 4,
        "name": "Oatmeal Honey",
        "tagline": "Nature's gentle touch",
        "price": 13.99,
        "image": "/static/images/oatmeal.svg",
        "gallery": ["/static/images/oatmeal.svg", "/static/images/oatmeal-alt.svg"],
        "category": "Nourishing",
        "scent": "vanilla",
        "skin_types": ["sensitive", "dry"],
        "ingredients": [
            "Colloidal Oatmeal", "Raw Manuka Honey", "Coconut Milk",
            "Sweet Almond Oil", "Vanilla Bean Extract",
            "Chamomile Extract", "Vitamin E"
        ],
        "benefits": [
            "Soothes sensitive and irritated skin",
            "Provides gentle natural exfoliation",
            "Locks in moisture for all-day comfort",
            "Calms eczema and dry patches"
        ],
        "description": (
            "Crafted with colloidal oatmeal, raw Manuka honey, and coconut milk, "
            "this gentle bar is a remedy for sensitive skin. The warm vanilla scent "
            "and chamomile extract create a soothing bathing experience."
        ),
        "weight": "115g",
        "rating": 4.6,
        "reviews": [
            {"author": "Nina W.", "rating": 5, "text": "A lifesaver for my eczema. So gentle and soothing.", "date": "2025-11-25"},
            {"author": "Tom H.", "rating": 4, "text": "Smells amazing and leaves skin super soft. Great daily bar.", "date": "2025-11-10"},
            {"author": "Rachel G.", "rating": 5, "text": "The honey and oatmeal combo is perfection. Will repurchase forever.", "date": "2025-10-28"},
        ],
        "featured": False,
    },
    {
        "id": 5,
        "name": "Citrus Burst",
        "tagline": "Energize your morning",
        "price": 14.49,
        "image": "/static/images/citrus.svg",
        "gallery": ["/static/images/citrus.svg", "/static/images/citrus-alt.svg"],
        "category": "Energizing",
        "scent": "citrus",
        "skin_types": ["oily", "dry"],
        "ingredients": [
            "Sweet Orange Essential Oil", "Lemon Zest",
            "Grapefruit Extract", "Coconut Oil",
            "Turmeric Powder", "Olive Oil", "Vitamin C"
        ],
        "benefits": [
            "Brightens and evens skin tone",
            "Antioxidant-rich formula protects skin",
            "Invigorating citrus aromatherapy",
            "Balances oil production naturally"
        ],
        "description": (
            "A vibrant blend of sweet orange, lemon zest, and grapefruit, infused "
            "with turmeric for its brightening properties. This energizing bar "
            "is the perfect way to start your day feeling refreshed."
        ),
        "weight": "110g",
        "rating": 4.5,
        "reviews": [
            {"author": "Alex J.", "rating": 5, "text": "The citrus scent wakes me up better than coffee. Fantastic soap.", "date": "2025-12-08"},
            {"author": "Mia C.", "rating": 4, "text": "Love the natural brightening effect. Great for morning showers.", "date": "2025-11-22"},
            {"author": "Ben S.", "rating": 4, "text": "Refreshing scent and nice lather. Good everyday soap.", "date": "2025-11-05"},
        ],
        "featured": False,
    },
    {
        "id": 6,
        "name": "Mint Eucalyptus",
        "tagline": "Breathe deeply",
        "price": 15.49,
        "image": "/static/images/mint.svg",
        "gallery": ["/static/images/mint.svg", "/static/images/mint-alt.svg"],
        "category": "Refreshing",
        "scent": "mint",
        "skin_types": ["oily", "sensitive"],
        "ingredients": [
            "Peppermint Essential Oil", "Eucalyptus Oil",
            "Green Clay", "Coconut Oil",
            "Spirulina Powder", "Aloe Vera", "Tea Tree Oil"
        ],
        "benefits": [
            "Cooling sensation soothes tired muscles",
            "Antibacterial properties keep skin clean",
            "Opens sinuses with menthol aromatherapy",
            "Balances oily skin without stripping"
        ],
        "description": (
            "A refreshing combination of peppermint and eucalyptus essential oils "
            "with green clay and spirulina. This invigorating bar provides a cooling "
            "sensation while gently purifying and balancing the skin."
        ),
        "weight": "110g",
        "rating": 4.7,
        "reviews": [
            {"author": "Sarah K.", "rating": 5, "text": "The cooling effect is amazing. Feels like a spa treatment.", "date": "2025-12-12"},
            {"author": "Daniel F.", "rating": 4, "text": "Perfect after a workout. Really refreshing and cleansing.", "date": "2025-11-30"},
            {"author": "Lisa M.", "rating": 5, "text": "Love the mint tingle. My skin feels so clean afterwards.", "date": "2025-11-14"},
        ],
        "featured": True,
    },
    {
        "id": 7,
        "name": "Coconut Vanilla",
        "tagline": "Tropical indulgence",
        "price": 15.99,
        "image": "/static/images/coconut.svg",
        "gallery": ["/static/images/coconut.svg", "/static/images/coconut-alt.svg"],
        "category": "Nourishing",
        "scent": "vanilla",
        "skin_types": ["dry"],
        "ingredients": [
            "Virgin Coconut Oil", "Vanilla Bean Extract",
            "Cocoa Butter", "Mango Butter",
            "Coconut Milk", "Raw Honey", "Vitamin E"
        ],
        "benefits": [
            "Ultra-rich moisturization for dry skin",
            "Creates a protective moisture barrier",
            "Warm vanilla aromatherapy for relaxation",
            "Leaves skin silky smooth all day"
        ],
        "description": (
            "A decadent fusion of virgin coconut oil, real vanilla bean, cocoa butter, "
            "and mango butter. This ultra-moisturizing bar wraps your skin in tropical "
            "luxury, perfect for those who crave deep hydration."
        ),
        "weight": "120g",
        "rating": 4.8,
        "reviews": [
            {"author": "Olivia R.", "rating": 5, "text": "Smells like a tropical vacation. My dry skin loves this soap.", "date": "2025-12-03"},
            {"author": "Chris P.", "rating": 5, "text": "The cocoa butter makes such a difference. Ultra moisturizing.", "date": "2025-11-19"},
            {"author": "Amy L.", "rating": 4, "text": "Beautiful soap. The vanilla scent is warm and comforting.", "date": "2025-11-02"},
        ],
        "featured": False,
    },
    {
        "id": 8,
        "name": "Turmeric Glow",
        "tagline": "Radiance revealed",
        "price": 17.49,
        "image": "/static/images/turmeric.svg",
        "gallery": ["/static/images/turmeric.svg", "/static/images/turmeric-alt.svg"],
        "category": "Brightening",
        "scent": "citrus",
        "skin_types": ["oily", "sensitive", "dry"],
        "ingredients": [
            "Organic Turmeric", "Raw Honey", "Sandalwood Powder",
            "Coconut Oil", "Lemon Essential Oil",
            "Neem Oil", "Aloe Vera Gel"
        ],
        "benefits": [
            "Brightens dull and uneven skin",
            "Reduces dark spots and hyperpigmentation",
            "Anti-inflammatory properties calm skin",
            "Natural antibacterial protection"
        ],
        "description": (
            "Harnessing the ancient wisdom of turmeric combined with raw honey, "
            "sandalwood, and neem oil. This golden bar brightens and evens skin tone "
            "while providing anti-inflammatory and antibacterial benefits."
        ),
        "weight": "115g",
        "rating": 4.6,
        "reviews": [
            {"author": "Priya N.", "rating": 5, "text": "My skin is glowing after just two weeks. This turmeric soap is incredible.", "date": "2025-12-14"},
            {"author": "Jessica A.", "rating": 4, "text": "Love the brightening effect. A little goes a long way.", "date": "2025-12-01"},
            {"author": "Ryan M.", "rating": 5, "text": "Great for acne-prone skin. The turmeric really works.", "date": "2025-11-16"},
        ],
        "featured": False,
    },
]

# All distinct values for filters
ALL_SKIN_TYPES = sorted({st for p in PRODUCTS for st in p["skin_types"]})
ALL_SCENTS = sorted({p["scent"] for p in PRODUCTS})

TESTIMONIALS = [
    {
        "text": "Luxe Lather transformed my skincare routine. The quality is unmatched — every bar feels like a spa experience at home.",
        "author": "Victoria S.",
        "title": "Loyal Customer",
    },
    {
        "text": "I have extremely sensitive skin and these are the only soaps that don't cause irritation. The ingredients are truly pure.",
        "author": "Dr. Amanda Chen",
        "title": "Dermatologist",
    },
    {
        "text": "The attention to detail in every bar is remarkable. From the packaging to the lather, everything screams luxury.",
        "author": "Michael Torres",
        "title": "Beauty Editor",
    },
]


# ---------------------------------------------------------------------------
# AI Recommendation Engine
# ---------------------------------------------------------------------------

def recommend_products(skin_type, scent_preference, top_n=4):
    """
    Score and rank products based on skin-type compatibility and scent
    preference.  Each product receives a composite score:

      score = skin_match_weight + scent_match_weight + rating_bonus

    - skin_match_weight  : 5 points if the product lists the user's skin type
    - scent_match_weight : 3 points if the scent matches preference
    - rating_bonus       : product rating (0-5) acts as a tiebreaker

    Returns the top-N products sorted by descending score.
    """
    scored = []
    for product in PRODUCTS:
        score = 0.0

        # Skin-type match (primary signal)
        if skin_type and skin_type.lower() in [s.lower() for s in product["skin_types"]]:
            score += 5.0

        # Scent preference match (secondary signal)
        if scent_preference and scent_preference.lower() == product["scent"].lower():
            score += 3.0

        # Rating as tiebreaker
        score += product["rating"]

        scored.append((score, product))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored[:top_n]]


# ---------------------------------------------------------------------------
# Routes — Pages
# ---------------------------------------------------------------------------

@app.route("/")
def homepage():
    featured = [p for p in PRODUCTS if p["featured"]]
    return render_template(
        "index.html",
        featured_products=featured,
        testimonials=TESTIMONIALS,
        skin_types=ALL_SKIN_TYPES,
        scents=ALL_SCENTS,
    )


@app.route("/products")
def product_listing():
    skin_filter = request.args.get("skin_type", "").lower()
    scent_filter = request.args.get("scent", "").lower()

    filtered = PRODUCTS
    if skin_filter:
        filtered = [p for p in filtered if skin_filter in [s.lower() for s in p["skin_types"]]]
    if scent_filter:
        filtered = [p for p in filtered if p["scent"].lower() == scent_filter]

    return render_template(
        "products.html",
        products=filtered,
        skin_types=ALL_SKIN_TYPES,
        scents=ALL_SCENTS,
        active_skin=skin_filter,
        active_scent=scent_filter,
    )


@app.route("/product/<int:product_id>")
def product_detail(product_id):
    product = next((p for p in PRODUCTS if p["id"] == product_id), None)
    if not product:
        return render_template("404.html"), 404
    related = [p for p in PRODUCTS if p["id"] != product_id and (
        p["scent"] == product["scent"] or
        set(p["skin_types"]) & set(product["skin_types"])
    )][:3]
    return render_template("product_detail.html", product=product, related_products=related)


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/cart")
def cart():
    return render_template("cart.html")


# ---------------------------------------------------------------------------
# Routes — API
# ---------------------------------------------------------------------------

@app.route("/api/recommend", methods=["POST"])
def api_recommend():
    """
    AI recommendation endpoint.
    Accepts JSON: { "skin_type": "dry", "scent": "lavender" }
    Returns JSON array of recommended products.
    """
    data = request.get_json(silent=True) or {}
    skin_type = data.get("skin_type", "")
    scent = data.get("scent", "")

    if not skin_type and not scent:
        return jsonify({"error": "Please provide at least a skin_type or scent preference."}), 400

    results = recommend_products(skin_type, scent)

    recommendations = []
    for p in results:
        recommendations.append({
            "id": p["id"],
            "name": p["name"],
            "tagline": p["tagline"],
            "price": p["price"],
            "image": p["image"],
            "scent": p["scent"],
            "skin_types": p["skin_types"],
            "rating": p["rating"],
            "description": p["description"],
        })

    return jsonify({"recommendations": recommendations})


@app.route("/api/cart", methods=["GET"])
def api_cart_get():
    cart_items = session.get("cart", [])
    return jsonify({"cart": cart_items, "total": sum(i["price"] * i["qty"] for i in cart_items)})


@app.route("/api/cart/add", methods=["POST"])
def api_cart_add():
    data = request.get_json(silent=True) or {}
    product_id = data.get("product_id")
    qty = data.get("qty", 1)

    product = next((p for p in PRODUCTS if p["id"] == product_id), None)
    if not product:
        return jsonify({"error": "Product not found"}), 404

    cart_items = session.get("cart", [])
    existing = next((i for i in cart_items if i["id"] == product_id), None)
    if existing:
        existing["qty"] += qty
    else:
        cart_items.append({
            "id": product["id"],
            "name": product["name"],
            "price": product["price"],
            "image": product["image"],
            "qty": qty,
        })

    session["cart"] = cart_items
    return jsonify({"cart": cart_items, "count": sum(i["qty"] for i in cart_items)})


@app.route("/api/cart/update", methods=["POST"])
def api_cart_update():
    data = request.get_json(silent=True) or {}
    product_id = data.get("product_id")
    qty = data.get("qty", 1)

    cart_items = session.get("cart", [])
    item = next((i for i in cart_items if i["id"] == product_id), None)
    if not item:
        return jsonify({"error": "Item not in cart"}), 404

    if qty <= 0:
        cart_items = [i for i in cart_items if i["id"] != product_id]
    else:
        item["qty"] = qty

    session["cart"] = cart_items
    return jsonify({"cart": cart_items, "total": sum(i["price"] * i["qty"] for i in cart_items)})


@app.route("/api/cart/remove", methods=["POST"])
def api_cart_remove():
    data = request.get_json(silent=True) or {}
    product_id = data.get("product_id")

    cart_items = session.get("cart", [])
    cart_items = [i for i in cart_items if i["id"] != product_id]
    session["cart"] = cart_items
    return jsonify({"cart": cart_items, "total": sum(i["price"] * i["qty"] for i in cart_items)})


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5001)
