import os
from collections import defaultdict
from datetime import datetime
from flask import Flask, render_template, url_for, flash, redirect, request, jsonify, session
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, login_user, current_user, logout_user, login_required
from sqlalchemy import text, or_, func, update
from werkzeug.utils import secure_filename
from models import db, User, Category, Product, Review, Order, OrderItem, ProductImage, SellerNotification
from forms import (
    RegistrationForm,
    LoginForm,
    ReviewForm,
    SellerProductForm,
    OrderStatusForm,
    CheckoutForm,
    AddToCartForm,
    CartUpdateForm,
    RemoveFromCartForm,
    CheckoutCartForm,
)

app = Flask(__name__)
app.config['SECRET_KEY'] = '5791628bb0b13ce0c676dfde280ba245'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['PRODUCT_UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads', 'products')

db.init_app(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'


def store_lower(value):
    if value is None:
        return None
    text = str(value).strip()
    return text.lower() if text else ''


def ensure_schema():
    # Lightweight migration for existing sqlite database.
    with db.engine.connect() as conn:
        product_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(product)"))}
        if "stock_count" not in product_columns:
            conn.execute(text("ALTER TABLE product ADD COLUMN stock_count INTEGER NOT NULL DEFAULT 0"))
        if "seller_id" not in product_columns:
            conn.execute(text("ALTER TABLE product ADD COLUMN seller_id INTEGER"))
        user_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(user)"))}
        if "is_seller" not in user_columns:
            conn.execute(text("ALTER TABLE user ADD COLUMN is_seller BOOLEAN NOT NULL DEFAULT 0"))

        existing_tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if "order" not in existing_tables:
            conn.execute(text("""
                CREATE TABLE "order" (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    total_amount FLOAT NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES user(id)
                )
            """))
        if "order_item" not in existing_tables:
            conn.execute(text("""
                CREATE TABLE order_item (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    quantity INTEGER NOT NULL,
                    price_at_purchase FLOAT NOT NULL,
                    FOREIGN KEY(order_id) REFERENCES "order"(id),
                    FOREIGN KEY(product_id) REFERENCES product(id)
                )
            """))
        if "product_image" not in existing_tables:
            conn.execute(text("""
                CREATE TABLE product_image (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL,
                    image_path VARCHAR(255) NOT NULL,
                    FOREIGN KEY(product_id) REFERENCES product(id)
                )
            """))

        if "seller_notification" not in existing_tables:
            conn.execute(text("""
                CREATE TABLE seller_notification (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    seller_id INTEGER NOT NULL,
                    order_id INTEGER NOT NULL,
                    message VARCHAR(500) NOT NULL,
                    is_read BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(seller_id) REFERENCES user(id),
                    FOREIGN KEY(order_id) REFERENCES "order"(id)
                )
            """))

        order_columns = {row[1] for row in conn.execute(text('PRAGMA table_info("order")'))}
        for col, ddl in [
            ("delivery_full_name", 'ALTER TABLE "order" ADD COLUMN delivery_full_name VARCHAR(120)'),
            ("delivery_phone", 'ALTER TABLE "order" ADD COLUMN delivery_phone VARCHAR(40)'),
            ("delivery_address", 'ALTER TABLE "order" ADD COLUMN delivery_address TEXT'),
            ("delivery_city", 'ALTER TABLE "order" ADD COLUMN delivery_city VARCHAR(80)'),
            ("delivery_postal", 'ALTER TABLE "order" ADD COLUMN delivery_postal VARCHAR(20)'),
            ("delivery_country", 'ALTER TABLE "order" ADD COLUMN delivery_country VARCHAR(80)'),
            ("payment_method", 'ALTER TABLE "order" ADD COLUMN payment_method VARCHAR(20)'),
            ("payment_card_holder", 'ALTER TABLE "order" ADD COLUMN payment_card_holder VARCHAR(120)'),
            ("payment_card_last4", 'ALTER TABLE "order" ADD COLUMN payment_card_last4 VARCHAR(8)'),
            ("guest_email", 'ALTER TABLE "order" ADD COLUMN guest_email VARCHAR(120)'),
        ]:
            if col not in order_columns:
                conn.execute(text(ddl))

        category_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(category)"))}
        if "parent_id" not in category_columns:
            conn.execute(text("ALTER TABLE category ADD COLUMN parent_id INTEGER REFERENCES category(id)"))

        conn.execute(text("UPDATE product SET name = lower(name), description = lower(description)"))
        conn.execute(text("UPDATE category SET name = lower(name)"))
        conn.execute(text("UPDATE user SET username = lower(username), email = lower(email)"))
        conn.execute(text("UPDATE review SET comment = lower(comment)"))
        conn.commit()


def save_uploaded_images(product, files):
    os.makedirs(app.config['PRODUCT_UPLOAD_FOLDER'], exist_ok=True)
    for image in files:
        if not image or not image.filename:
            continue
        safe_name = secure_filename(image.filename)
        if not safe_name:
            continue
        unique_name = f"{product.id}_{int(datetime.utcnow().timestamp())}_{safe_name}"
        full_path = os.path.join(app.config['PRODUCT_UPLOAD_FOLDER'], unique_name)
        image.save(full_path)
        relative_path = f"/static/uploads/products/{unique_name}"
        db.session.add(ProductImage(product_id=product.id, image_path=relative_path))


def notify_sellers_for_order(order):
    by_seller = defaultdict(list)
    for item in order.items:
        product = item.product
        if product and product.seller_id:
            by_seller[product.seller_id].append(item)

    for seller_id, items in by_seller.items():
        parts = [f"{it.product.name} x{it.quantity}" for it in items if it.product]
        msg = "new order: " + ", ".join(parts)
        db.session.add(
            SellerNotification(
                seller_id=seller_id,
                order_id=order.id,
                message=store_lower(msg),
            )
        )


def product_to_dict(product):
    return {
        "id": product.id,
        "name": product.name,
        "description": product.description,
        "price": product.price,
        "image_url": product.image_url,
        "category_id": product.category_id,
        "stock_count": product.stock_count,
    }


def get_cart_dict():
    raw = session.get("cart")
    if not isinstance(raw, dict):
        return {}
    cleaned = {}
    for k, v in raw.items():
        try:
            pid = int(k)
            qty = int(v)
            if pid > 0 and qty > 0:
                cleaned[str(pid)] = qty
        except (TypeError, ValueError):
            continue
    return cleaned


def save_cart_dict(cart_dict):
    session["cart"] = cart_dict
    session.modified = True


def cart_line_count():
    return sum(get_cart_dict().values())


def resolve_cart_lines():
    """Return list of {product, quantity, line_total, max_qty} and sync session to stock limits."""
    cart = get_cart_dict()
    lines = []
    new_cart = {}
    for pid_str, qty in cart.items():
        product = Product.query.get(int(pid_str))
        if not product or product.stock_count < 1:
            continue
        q = min(int(qty), product.stock_count)
        if q < 1:
            continue
        new_cart[pid_str] = q
        line_total = round(product.price * q, 2)
        lines.append(
            {
                "product": product,
                "quantity": q,
                "line_total": line_total,
                "max_qty": product.stock_count,
            }
        )
    if new_cart != cart:
        save_cart_dict(new_cart)
    total = round(sum(line["line_total"] for line in lines), 2)
    return lines, total


@app.context_processor
def inject_cart_count():
    return {"cart_count": cart_line_count()}


def order_to_dict(order):
    return {
        "id": order.id,
        "user_id": order.user_id,
        "status": order.status,
        "total_amount": order.total_amount,
        "created_at": order.created_at.isoformat(),
        "items": [
            {
                "product_id": item.product_id,
                "product_name": item.product.name if item.product else None,
                "quantity": item.quantity,
                "price_at_purchase": item.price_at_purchase,
            }
            for item in order.items
        ],
    }


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def category_tree_ids(root):
    ids = {root.id}
    for child in Category.query.filter_by(parent_id=root.id).order_by(Category.name.asc()).all():
        ids |= category_tree_ids(child)
    return ids


def categories_for_seller_form():
    cats = Category.query.all()
    cats.sort(key=lambda c: ((c.parent.name if c.parent else ""), c.name))
    return [(c.id, f"{c.parent.name} → {c.name}" if c.parent else c.name) for c in cats]


def ensure_catalog():
    """Idempotent: parent categories, subcategories, product migration, and demo catalog."""
    clothes = Category.query.filter(
        func.lower(Category.name) == "clothes",
        Category.parent_id.is_(None),
    ).first()
    makeup = Category.query.filter(
        func.lower(Category.name) == "makeup",
        Category.parent_id.is_(None),
    ).first()

    if not clothes:
        clothes = Category(name="clothes", parent_id=None)
        db.session.add(clothes)
        db.session.flush()
    if not makeup:
        makeup = Category(name="makeup", parent_id=None)
        db.session.add(makeup)
        db.session.flush()

    cloth_subs = ["dresses", "tops", "outerwear", "bottoms", "knitwear"]
    makeup_subs = ["face", "lips", "eyes", "tools"]

    sub_map = {}
    for slug in cloth_subs:
        c = Category.query.filter(func.lower(Category.name) == slug).first()
        if not c:
            c = Category(name=slug, parent_id=clothes.id)
            db.session.add(c)
            db.session.flush()
        elif c.parent_id != clothes.id:
            c.parent_id = clothes.id
        sub_map[slug] = c

    for slug in makeup_subs:
        c = Category.query.filter(func.lower(Category.name) == slug).first()
        if not c:
            c = Category(name=slug, parent_id=makeup.id)
            db.session.add(c)
            db.session.flush()
        elif c.parent_id != makeup.id:
            c.parent_id = makeup.id
        sub_map[slug] = c

    migrate_names = {
        "silk evening dress": "dresses",
        "floral summer dress": "dresses",
        "cashmere overcoat": "outerwear",
        "velvet matte lipstick": "lips",
        "luminous foundation": "face",
        "eyeshadow palette": "eyes",
    }
    for pname, sub_slug in migrate_names.items():
        p = Product.query.filter(func.lower(Product.name) == pname).first()
        sub = sub_map.get(sub_slug)
        if p and sub:
            p.category_id = sub.id

    seed_rows = [
        ("silk evening dress", "an elegant silk evening dress with a flattering silhouette. perfect for special occasions.", 129.0, "https://images.unsplash.com/photo-1595777457583-95e059d581b8?w=400&h=530&fit=crop", 20, "dresses"),
        ("cashmere overcoat", "luxurious cashmere overcoat in classic beige. timeless warmth meets modern elegance.", 245.0, "https://images.unsplash.com/photo-1539533113208-f6df8cc8b543?w=400&h=530&fit=crop", 15, "outerwear"),
        ("floral summer dress", "light and breezy floral dress, perfect for sunny days and garden parties.", 85.0, "https://images.unsplash.com/photo-1572804013309-59a88b7e92f1?w=400&h=530&fit=crop", 25, "dresses"),
        ("velvet matte lipstick", "long-lasting velvet matte finish in classic red. bold color that stays all day.", 32.0, "https://images.unsplash.com/photo-1586495777744-4413f21062fa?w=400&h=530&fit=crop", 40, "lips"),
        ("luminous foundation", "lightweight liquid foundation with buildable coverage and a natural dewy finish.", 48.0, "https://images.unsplash.com/photo-1631214524020-7e18db9a8f92?w=400&h=530&fit=crop", 30, "face"),
        ("eyeshadow palette", "12-shade palette with matte and shimmer finishes. create endless looks from day to night.", 55.0, "https://images.unsplash.com/photo-1512496015851-a90fb38ba796?w=400&h=530&fit=crop", 18, "eyes"),
        ("satin slip dress", "minimal satin midi slip dress for evenings and layering.", 98.0, "https://images.unsplash.com/photo-1595777457583-95e059d581b8?w=400&h=530&fit=crop", 22, "dresses"),
        ("linen blend shirt", "breathable linen-cotton shirt with relaxed tailored fit.", 72.0, "https://images.unsplash.com/photo-1594938298603-c8148c4dae35?w=400&h=530&fit=crop", 28, "tops"),
        ("ribbed tank top", "soft ribbed tank ideal for layering or solo summer wear.", 38.0, "https://images.unsplash.com/photo-1509631179647-0177331693ae?w=400&h=530&fit=crop", 35, "tops"),
        ("wool blazer", "structured wool blazer in charcoal for office and events.", 198.0, "https://images.unsplash.com/photo-1594938298603-c8148c4dae35?w=400&h=530&fit=crop", 14, "outerwear"),
        ("high waist trousers", "tailored high waist trousers with pressed crease.", 112.0, "https://images.unsplash.com/photo-1541099649105-f69ad21a3245?w=400&h=530&fit=crop", 20, "bottoms"),
        ("wide leg jeans", "soft denim wide leg jeans with vintage wash.", 89.0, "https://images.unsplash.com/photo-1542272604-787c3835535d?w=400&h=530&fit=crop", 24, "bottoms"),
        ("cable knit sweater", "chunky cable knit crewneck in cream wool blend.", 118.0, "https://images.unsplash.com/photo-1576566588028-4147f3842f27?w=400&h=530&fit=crop", 18, "knitwear"),
        ("radiant concealer", "creamy concealer with brightening finish for under eyes.", 29.0, "https://images.unsplash.com/photo-1616683693503-3b7e39ef7879?w=400&h=530&fit=crop", 36, "face"),
        ("setting powder", "translucent loose powder to lock makeup all day.", 34.0, "https://images.unsplash.com/photo-1631214524020-7e18db9a8f92?w=400&h=530&fit=crop", 40, "face"),
        ("gloss balm duo", "hydrating tinted gloss balm set in rose and nude.", 26.0, "https://images.unsplash.com/photo-1586495777744-4413f21062fa?w=400&h=530&fit=crop", 42, "lips"),
        ("volumizing mascara", "buildable black mascara for length and volume.", 24.0, "https://images.unsplash.com/photo-1631214524020-7e18db9a8f92?w=400&h=530&fit=crop", 50, "eyes"),
        ("brush set", "7-piece synthetic brush set for face and eyes.", 44.0, "https://images.unsplash.com/photo-1512496015851-a90fb38ba796?w=400&h=530&fit=crop", 25, "tools"),
    ]

    for name, desc, price, img, stock, sub_slug in seed_rows:
        key = store_lower(name)
        existing = Product.query.filter(func.lower(Product.name) == key).first()
        sub = sub_map.get(sub_slug)
        if existing or not sub:
            continue
        db.session.add(
            Product(
                name=key,
                description=store_lower(desc),
                price=price,
                image_url=img,
                category_id=sub.id,
                stock_count=stock,
            )
        )

    db.session.commit()


@app.route("/")
@app.route("/home")
def home():
    try:
        featured_products = Product.query.order_by(Product.id.desc()).limit(12).all()
        root_categories = (
            Category.query.filter(Category.parent_id.is_(None))
            .order_by(Category.name.asc())
            .all()
        )
        return render_template(
            "home.html",
            products=featured_products,
            root_categories=root_categories,
        )
    except Exception as e:
        app.logger.error(f"Error on home page: {e}")
        return f"An error occurred: {e}", 500

@app.route("/signup", methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        if current_user.is_seller:
            return redirect(url_for('seller_dashboard'))
        return redirect(url_for('home'))
    form = RegistrationForm()
    if form.validate_on_submit():
        hashed_password = bcrypt.generate_password_hash(form.password.data).decode('utf-8')
        user = User(
            username=store_lower(form.username.data),
            email=store_lower(form.email.data),
            password_hash=hashed_password,
            is_seller=False,
        )
        db.session.add(user)
        db.session.commit()
        flash('Your account has been created! You are now able to log in', 'success')
        return redirect(url_for('login'))
    return render_template('signup.html', title='Sign Up', form=form)

@app.route("/login", methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if current_user.is_seller:
            return redirect(url_for('seller_dashboard'))
        return redirect(url_for('home'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter(func.lower(User.email) == store_lower(form.email.data)).first()
        if user and bcrypt.check_password_hash(user.password_hash, form.password.data):
            if user.is_seller:
                flash('This account is a seller account. Please use seller login.', 'info')
                return redirect(url_for('seller_login'))
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('home'))
        else:
            flash('Login Unsuccessful. Please check email and password', 'danger')
    return render_template('login.html', title='Login', form=form)

@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for('home'))


@app.route("/seller/signup", methods=['GET', 'POST'])
def seller_signup():
    if current_user.is_authenticated:
        if current_user.is_seller:
            return redirect(url_for('seller_dashboard'))
        return redirect(url_for('home'))
    form = RegistrationForm()
    if form.validate_on_submit():
        hashed_password = bcrypt.generate_password_hash(form.password.data).decode('utf-8')
        user = User(
            username=store_lower(form.username.data),
            email=store_lower(form.email.data),
            password_hash=hashed_password,
            is_seller=True,
        )
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash('Seller account created successfully.', 'success')
        return redirect(url_for('seller_dashboard'))
    return render_template('seller_signup.html', title='Seller Sign Up', form=form)


@app.route("/seller/login", methods=['GET', 'POST'])
def seller_login():
    if current_user.is_authenticated:
        if current_user.is_seller:
            return redirect(url_for('seller_dashboard'))
        return redirect(url_for('home'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter(func.lower(User.email) == store_lower(form.email.data)).first()
        if user and bcrypt.check_password_hash(user.password_hash, form.password.data):
            if not user.is_seller:
                flash('This account is a user account. Please use user login.', 'info')
                return redirect(url_for('login'))
            login_user(user)
            return redirect(url_for('seller_dashboard'))
        flash('Login Unsuccessful. Please check email and password', 'danger')
    return render_template('seller_login.html', title='Seller Login', form=form)


@app.route("/seller/dashboard", methods=["GET"])
@login_required
def seller_dashboard():
    products = Product.query.filter_by(seller_id=current_user.id).order_by(Product.id.desc()).all()
    notifications = (
        SellerNotification.query.filter_by(seller_id=current_user.id)
        .order_by(SellerNotification.created_at.desc())
        .limit(20)
        .all()
    )
    unread_note_count = SellerNotification.query.filter_by(seller_id=current_user.id, is_read=False).count()
    return render_template(
        "seller_dashboard.html",
        title="Seller Dashboard",
        products=products,
        notifications=notifications,
        unread_note_count=unread_note_count,
    )


@app.route("/seller/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def seller_mark_notification_read(notification_id):
    note = SellerNotification.query.filter_by(id=notification_id, seller_id=current_user.id).first_or_404()
    note.is_read = True
    db.session.commit()
    flash("Notification dismissed.", "success")
    return redirect(request.referrer or url_for("seller_dashboard"))


@app.route("/seller/products/new", methods=["GET", "POST"])
@login_required
def seller_new_product():
    form = SellerProductForm()
    form.category_id.choices = categories_for_seller_form()

    if form.validate_on_submit():
        product = Product(
            name=store_lower(form.name.data),
            description=store_lower(form.description.data),
            price=form.price.data,
            category_id=form.category_id.data,
            seller_id=current_user.id,
            stock_count=form.stock_count.data,
        )
        db.session.add(product)
        db.session.flush()
        save_uploaded_images(product, request.files.getlist("product_images"))
        db.session.commit()
        flash("Product created successfully.", "success")
        return redirect(url_for("seller_dashboard"))

    return render_template("seller_product_form.html", title="Add Product", form=form, is_edit=False)


@app.route("/seller/products/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def seller_edit_product(product_id):
    product = Product.query.filter_by(id=product_id, seller_id=current_user.id).first_or_404()
    form = SellerProductForm(obj=product)
    form.category_id.choices = categories_for_seller_form()

    if form.validate_on_submit():
        product.name = store_lower(form.name.data)
        product.description = store_lower(form.description.data)
        product.price = form.price.data
        product.category_id = form.category_id.data
        product.stock_count = form.stock_count.data
        save_uploaded_images(product, request.files.getlist("product_images"))
        db.session.commit()
        flash("Product updated successfully.", "success")
        return redirect(url_for("seller_dashboard"))

    return render_template("seller_product_form.html", title="Edit Product", form=form, is_edit=True, product=product)


@app.route("/seller/products/<int:product_id>/delete", methods=["POST"])
@login_required
def seller_delete_product(product_id):
    product = Product.query.filter_by(id=product_id, seller_id=current_user.id).first_or_404()
    for image in product.images:
        file_path = os.path.join(app.root_path, image.image_path.lstrip("/").replace("/", os.sep))
        if os.path.exists(file_path):
            os.remove(file_path)
    db.session.delete(product)
    db.session.commit()
    flash("Product deleted successfully.", "success")
    return redirect(url_for("seller_dashboard"))


@app.route("/seller/orders", methods=["GET"])
@login_required
def seller_orders():
    orders = (
        Order.query
        .join(Order.items)
        .join(OrderItem.product)
        .filter(Product.seller_id == current_user.id)
        .order_by(Order.created_at.desc())
        .distinct()
        .all()
    )
    seller_items_by_order = {}
    seller_totals = {}
    forms = {}
    for order in orders:
        seller_items = [item for item in order.items if item.product and item.product.seller_id == current_user.id]
        seller_items_by_order[order.id] = seller_items
        seller_totals[order.id] = sum(item.quantity * item.price_at_purchase for item in seller_items)
        form = OrderStatusForm(prefix=f"order-{order.id}")
        form.status.data = order.status
        forms[order.id] = form
    return render_template(
        "seller_orders.html",
        title="Seller Orders",
        orders=orders,
        forms=forms,
        seller_items_by_order=seller_items_by_order,
        seller_totals=seller_totals,
    )


@app.route("/seller/orders/<int:order_id>/status", methods=["POST"])
@login_required
def seller_update_order_status(order_id):
    order = Order.query.get_or_404(order_id)
    seller_has_items = any(item.product and item.product.seller_id == current_user.id for item in order.items)
    if not seller_has_items:
        flash("You are not allowed to update this order.", "danger")
        return redirect(url_for("seller_orders"))
    form = OrderStatusForm(prefix=f"order-{order.id}")
    if form.validate_on_submit():
        order.status = form.status.data
        db.session.commit()
        flash(f"Order #{order.id} status updated.", "success")
    else:
        flash("Could not update order status.", "danger")
    return redirect(url_for("seller_orders"))

def _escape_like_pattern(term):
    return (
        term.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


@app.route("/search")
def search():
    raw = (request.args.get("q") or "").strip()
    if not raw:
        products = []
    else:
        pattern = f"%{_escape_like_pattern(raw.lower())}%"
        products = (
            Product.query.filter(
                or_(
                    func.lower(Product.name).like(pattern, escape="\\"),
                    func.lower(Product.description).like(pattern, escape="\\"),
                )
            )
            .order_by(Product.name.asc())
            .all()
        )
    return render_template(
        "search.html",
        title="Search",
        products=products,
        query=raw,
    )


@app.route("/category/<string:category_name>")
def category(category_name):
    key = store_lower(category_name)
    category = Category.query.filter(func.lower(Category.name) == key).first_or_404()
    cat_ids = category_tree_ids(category)
    products = (
        Product.query.filter(Product.category_id.in_(cat_ids))
        .order_by(Product.name.asc())
        .all()
    )
    subcategories = (
        Category.query.filter_by(parent_id=category.id).order_by(Category.name.asc()).all()
    )
    display_title = category.name
    return render_template(
        "category.html",
        title=display_title,
        products=products,
        category=category,
        subcategories=subcategories,
    )

@app.route("/product/<int:product_id>", methods=['GET', 'POST'])
def product_detail(product_id):
    product = Product.query.get_or_404(product_id)
    form = ReviewForm()
    add_cart_form = AddToCartForm()
    add_cart_form.quantity.data = 1
    if form.validate_on_submit():
        if not current_user.is_authenticated:
            flash('Please login to post a review', 'info')
            return redirect(url_for('login'))
        review = Review(rating=form.rating.data, comment=store_lower(form.comment.data), author=current_user, product=product)
        db.session.add(review)
        db.session.commit()
        flash('Your review has been posted!', 'success')
        return redirect(url_for('product_detail', product_id=product.id))
    return render_template(
        'product_detail.html',
        title=product.name,
        product=product,
        form=form,
        add_cart_form=add_cart_form,
    )


@app.route("/checkout/<int:product_id>", methods=["GET", "POST"])
def checkout(product_id):
    product = Product.query.get_or_404(product_id)
    if product.stock_count < 1:
        flash("This product is out of stock.", "danger")
        return redirect(url_for("product_detail", product_id=product_id))

    form = CheckoutForm(max_stock=product.stock_count)
    if form.validate_on_submit():
        qty = int(form.quantity.data)

        stmt = (
            update(Product)
            .where(Product.id == product_id, Product.stock_count >= qty)
            .values(stock_count=Product.stock_count - qty)
        )
        result = db.session.execute(stmt)
        if result.rowcount != 1:
            db.session.rollback()
            flash("Not enough stock for this product.", "danger")
            return redirect(url_for("checkout", product_id=product_id))

        db.session.refresh(product)

        payment_method = form.payment_method.data
        card_holder = store_lower(form.payment_card_holder.data) if payment_method == "card" else None
        card_last4 = form.payment_card_last4.data.strip() if payment_method == "card" else None
        guest_email = None
        if current_user.is_authenticated:
            user_id = current_user.id
        else:
            user_id = None
            guest_email = store_lower(form.guest_email.data)

        order = Order(
            user_id=user_id,
            status="pending",
            total_amount=round(product.price * qty, 2),
            delivery_full_name=store_lower(form.delivery_full_name.data),
            delivery_phone=store_lower(form.delivery_phone.data),
            delivery_address=store_lower(form.delivery_address.data),
            delivery_city=store_lower(form.delivery_city.data),
            delivery_postal=store_lower(form.delivery_postal.data),
            delivery_country=store_lower(form.delivery_country.data),
            payment_method=payment_method,
            payment_card_holder=card_holder,
            payment_card_last4=card_last4,
            guest_email=guest_email,
        )
        db.session.add(order)
        db.session.flush()

        db.session.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                quantity=qty,
                price_at_purchase=product.price,
            )
        )
        db.session.flush()
        notify_sellers_for_order(order)
        db.session.commit()
        flash(f"Order #{order.id} placed successfully.", "success")
        return redirect(url_for("product_detail", product_id=product.id))

    return render_template(
        "checkout.html",
        title="checkout",
        form=form,
        product=product,
    )


@app.route("/cart/add/<int:product_id>", methods=["POST"])
def cart_add(product_id):
    product = Product.query.get_or_404(product_id)
    if product.stock_count < 1:
        flash("This product is out of stock.", "danger")
        return redirect(url_for("product_detail", product_id=product_id))
    form = AddToCartForm()
    if not form.validate_on_submit():
        flash("Please enter a valid quantity.", "danger")
        return redirect(url_for("product_detail", product_id=product_id))
    qty = int(form.quantity.data)
    cart = get_cart_dict()
    key = str(product_id)
    in_cart = cart.get(key, 0)
    if qty > product.stock_count or in_cart + qty > product.stock_count:
        flash("Not enough stock for that quantity (including items already in your cart).", "danger")
        return redirect(url_for("product_detail", product_id=product_id))
    cart[key] = in_cart + qty
    save_cart_dict(cart)
    flash("Added to cart.", "success")
    return redirect(request.referrer or url_for("view_cart"))


@app.route("/cart", methods=["GET"])
def view_cart():
    lines, total = resolve_cart_lines()
    update_forms = {}
    remove_forms = {}
    for line in lines:
        pid = line["product"].id
        prefix_u = f"u{pid}"
        uf = CartUpdateForm(prefix=prefix_u)
        uf.quantity.data = line["quantity"]
        update_forms[pid] = uf
        rf = RemoveFromCartForm(prefix=f"r{pid}")
        remove_forms[pid] = rf
    return render_template(
        "cart.html",
        title="Shopping cart",
        cart_lines=lines,
        cart_total=total,
        update_forms=update_forms,
        remove_forms=remove_forms,
    )


@app.route("/cart/update/<int:product_id>", methods=["POST"])
def cart_update(product_id):
    Product.query.get_or_404(product_id)
    form = CartUpdateForm(prefix=f"u{product_id}")
    if not form.validate_on_submit():
        flash("Invalid quantity.", "danger")
        return redirect(url_for("view_cart"))
    product = Product.query.get_or_404(product_id)
    qty = int(form.quantity.data)
    cart = get_cart_dict()
    key = str(product_id)
    if qty < 1:
        cart.pop(key, None)
    elif qty > product.stock_count:
        flash("Quantity exceeds available stock.", "danger")
        return redirect(url_for("view_cart"))
    else:
        cart[key] = qty
    save_cart_dict(cart)
    flash("Cart updated.", "success")
    return redirect(url_for("view_cart"))


@app.route("/cart/remove/<int:product_id>", methods=["POST"])
def cart_remove(product_id):
    form = RemoveFromCartForm(prefix=f"r{product_id}")
    if not form.validate_on_submit():
        flash("Could not remove item.", "danger")
        return redirect(url_for("view_cart"))
    cart = get_cart_dict()
    cart.pop(str(product_id), None)
    save_cart_dict(cart)
    flash("Removed from cart.", "success")
    return redirect(url_for("view_cart"))


@app.route("/checkout/cart", methods=["GET", "POST"])
def checkout_cart():
    lines, total = resolve_cart_lines()
    if not lines:
        flash("Your cart is empty.", "info")
        return redirect(url_for("view_cart"))

    form = CheckoutCartForm()
    if form.validate_on_submit():
        line_specs = []
        for line in lines:
            p = Product.query.get(line["product"].id)
            if not p or p.stock_count < 1:
                continue
            q = min(line["quantity"], p.stock_count)
            if q >= 1:
                line_specs.append((p, q))

        if not line_specs:
            flash("No items available to purchase.", "danger")
            return redirect(url_for("view_cart"))

        payment_method = form.payment_method.data
        card_holder = store_lower(form.payment_card_holder.data) if payment_method == "card" else None
        card_last4 = form.payment_card_last4.data.strip() if payment_method == "card" else None
        guest_email = None
        if current_user.is_authenticated:
            user_id = current_user.id
        else:
            user_id = None
            guest_email = store_lower(form.guest_email.data)

        for p, qty in line_specs:
            stmt = (
                update(Product)
                .where(Product.id == p.id, Product.stock_count >= qty)
                .values(stock_count=Product.stock_count - qty)
            )
            result = db.session.execute(stmt)
            if result.rowcount != 1:
                db.session.rollback()
                flash("Stock changed while checking out. Please review your cart.", "danger")
                return redirect(url_for("view_cart"))

        line_prices = []
        order_total = 0.0
        for p, qty in line_specs:
            db.session.refresh(p)
            price = p.price
            line_prices.append((p.id, qty, price))
            order_total += price * qty

        order = Order(
            user_id=user_id,
            status="pending",
            total_amount=round(order_total, 2),
            delivery_full_name=store_lower(form.delivery_full_name.data),
            delivery_phone=store_lower(form.delivery_phone.data),
            delivery_address=store_lower(form.delivery_address.data),
            delivery_city=store_lower(form.delivery_city.data),
            delivery_postal=store_lower(form.delivery_postal.data),
            delivery_country=store_lower(form.delivery_country.data),
            payment_method=payment_method,
            payment_card_holder=card_holder,
            payment_card_last4=card_last4,
            guest_email=guest_email,
        )
        db.session.add(order)
        db.session.flush()

        for pid, qty, price in line_prices:
            db.session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=pid,
                    quantity=qty,
                    price_at_purchase=price,
                )
            )
        db.session.flush()
        notify_sellers_for_order(order)
        save_cart_dict({})
        db.session.commit()
        flash(f"Order #{order.id} placed successfully.", "success")
        return redirect(url_for("home"))

    return render_template(
        "checkout_cart.html",
        title="Checkout",
        form=form,
        cart_lines=lines,
        cart_total=total,
    )


@app.route("/api/products", methods=["GET"])
def api_get_products():
    products = Product.query.order_by(Product.id.desc()).all()
    return jsonify([product_to_dict(product) for product in products]), 200


@app.route("/api/products", methods=["POST"])
def api_create_product():
    data = request.get_json(silent=True) or {}
    required = ["name", "description", "price", "category_id"]
    missing = [field for field in required if field not in data]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    category = Category.query.get(data["category_id"])
    if not category:
        return jsonify({"error": "Category not found"}), 404

    product = Product(
        name=store_lower(data["name"]),
        description=store_lower(data["description"]),
        price=float(data["price"]),
        image_url=data.get("image_url"),
        category_id=data["category_id"],
        stock_count=max(0, int(data.get("stock_count", 0))),
    )
    db.session.add(product)
    db.session.commit()
    return jsonify(product_to_dict(product)), 201


@app.route("/api/products/<int:product_id>", methods=["PUT"])
def api_update_product(product_id):
    product = Product.query.get_or_404(product_id)
    data = request.get_json(silent=True) or {}

    if "name" in data:
        product.name = store_lower(data["name"])
    if "description" in data:
        product.description = store_lower(data["description"])
    if "price" in data:
        product.price = float(data["price"])
    if "image_url" in data:
        product.image_url = data["image_url"]
    if "category_id" in data:
        category = Category.query.get(data["category_id"])
        if not category:
            return jsonify({"error": "Category not found"}), 404
        product.category_id = data["category_id"]
    if "stock_count" in data:
        product.stock_count = max(0, int(data["stock_count"]))

    db.session.commit()
    return jsonify(product_to_dict(product)), 200


@app.route("/api/products/<int:product_id>/stock", methods=["PATCH"])
def api_update_product_stock(product_id):
    product = Product.query.get_or_404(product_id)
    data = request.get_json(silent=True) or {}

    if "stock_count" in data:
        product.stock_count = max(0, int(data["stock_count"]))
    elif "change_by" in data:
        product.stock_count = max(0, product.stock_count + int(data["change_by"]))
    else:
        return jsonify({"error": "Provide stock_count or change_by"}), 400

    db.session.commit()
    return jsonify(product_to_dict(product)), 200


@app.route("/api/products/<int:product_id>", methods=["DELETE"])
def api_delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    return jsonify({"message": "Product deleted successfully"}), 200


@app.route("/api/orders", methods=["GET"])
def api_get_orders():
    orders = Order.query.order_by(Order.created_at.desc()).all()
    return jsonify([order_to_dict(order) for order in orders]), 200


@app.route("/api/orders/<int:order_id>", methods=["GET"])
def api_get_order(order_id):
    order = Order.query.get_or_404(order_id)
    return jsonify(order_to_dict(order)), 200


@app.route("/api/orders", methods=["POST"])
def api_create_order():
    data = request.get_json(silent=True) or {}
    items = data.get("items", [])
    if not items:
        return jsonify({"error": "Order must include at least one item"}), 400

    order = Order(
        user_id=data.get("user_id"),
        status=data.get("status", "pending"),
        total_amount=0.0,
    )
    db.session.add(order)

    total = 0.0
    line_prices = []
    for item in items:
        pid = item.get("product_id")
        quantity = int(item.get("quantity", 0))
        product = Product.query.get(pid) if pid is not None else None

        if not product or quantity <= 0:
            db.session.rollback()
            return jsonify({"error": "Invalid product or quantity in items"}), 400

        stmt = (
            update(Product)
            .where(Product.id == product.id, Product.stock_count >= quantity)
            .values(stock_count=Product.stock_count - quantity)
        )
        result = db.session.execute(stmt)
        if result.rowcount != 1:
            db.session.rollback()
            return jsonify({"error": f"Not enough stock for product {product.id}"}), 400

        db.session.refresh(product)
        line_total = product.price * quantity
        total += line_total
        line_prices.append((product.id, quantity, product.price))

    for pid, quantity, price in line_prices:
        db.session.add(OrderItem(
            order=order,
            product_id=pid,
            quantity=quantity,
            price_at_purchase=price,
        ))

    order.total_amount = total
    db.session.flush()
    notify_sellers_for_order(order)
    db.session.commit()
    return jsonify(order_to_dict(order)), 201


@app.route("/api/orders/<int:order_id>/status", methods=["PATCH"])
def api_update_order_status(order_id):
    order = Order.query.get_or_404(order_id)
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    allowed_statuses = {"pending", "paid", "shipped", "delivered", "cancelled"}
    if new_status not in allowed_statuses:
        return jsonify({"error": f"Status must be one of: {', '.join(sorted(allowed_statuses))}"}), 400

    order.status = new_status
    db.session.commit()
    return jsonify(order_to_dict(order)), 200

def init_db():
    with app.app_context():
        db.create_all()
        ensure_schema()
        ensure_catalog()


with app.app_context():
    db.create_all()
    ensure_schema()
    ensure_catalog()


if __name__ == '__main__':
    init_db()
    app.run(debug=True)
