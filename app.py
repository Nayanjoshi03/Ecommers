import os
from flask import Flask, render_template, url_for, flash, redirect, request, jsonify
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, login_user, current_user, logout_user, login_required
from sqlalchemy import text
from models import db, User, Category, Product, Review, Order, OrderItem
from forms import RegistrationForm, LoginForm, ReviewForm

app = Flask(__name__)
app.config['SECRET_KEY'] = '5791628bb0b13ce0c676dfde280ba245'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'

db.init_app(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'


def ensure_schema():
    # Lightweight migration for existing sqlite database.
    with db.engine.connect() as conn:
        product_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(product)"))}
        if "stock_count" not in product_columns:
            conn.execute(text("ALTER TABLE product ADD COLUMN stock_count INTEGER NOT NULL DEFAULT 0"))

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
        conn.commit()


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

@app.route("/")
@app.route("/home")
def home():
    try:
        products = db.session.execute(db.select(Product)).scalars().all()
        return render_template('home.html', products=products)
    except Exception as e:
        app.logger.error(f"Error on home page: {e}")
        return f"An error occurred: {e}", 500

@app.route("/signup", methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    form = RegistrationForm()
    if form.validate_on_submit():
        hashed_password = bcrypt.generate_password_hash(form.password.data).decode('utf-8')
        user = User(username=form.username.data, email=form.email.data, password_hash=hashed_password)
        db.session.add(user)
        db.session.commit()
        flash('Your account has been created! You are now able to log in', 'success')
        return redirect(url_for('login'))
    return render_template('signup.html', title='Sign Up', form=form)

@app.route("/login", methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and bcrypt.check_password_hash(user.password_hash, form.password.data):
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

@app.route("/category/<string:category_name>")
def category(category_name):
    category = Category.query.filter_by(name=category_name).first_or_404()
    products = Product.query.filter_by(category_id=category.id).all()
    return render_template('category.html', title=category_name, products=products, category=category)

@app.route("/product/<int:product_id>", methods=['GET', 'POST'])
def product_detail(product_id):
    product = Product.query.get_or_404(product_id)
    form = ReviewForm()
    if form.validate_on_submit():
        if not current_user.is_authenticated:
            flash('Please login to post a review', 'info')
            return redirect(url_for('login'))
        review = Review(rating=form.rating.data, comment=form.comment.data, author=current_user, product=product)
        db.session.add(review)
        db.session.commit()
        flash('Your review has been posted!', 'success')
        return redirect(url_for('product_detail', product_id=product.id))
    return render_template('product_detail.html', title=product.name, product=product, form=form)


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
        name=data["name"],
        description=data["description"],
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
        product.name = data["name"]
    if "description" in data:
        product.description = data["description"]
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
    for item in items:
        product = Product.query.get(item.get("product_id"))
        quantity = int(item.get("quantity", 0))

        if not product or quantity <= 0:
            db.session.rollback()
            return jsonify({"error": "Invalid product or quantity in items"}), 400
        if product.stock_count < quantity:
            db.session.rollback()
            return jsonify({"error": f"Not enough stock for product {product.id}"}), 400

        product.stock_count -= quantity
        line_total = product.price * quantity
        total += line_total
        db.session.add(OrderItem(
            order=order,
            product_id=product.id,
            quantity=quantity,
            price_at_purchase=product.price,
        ))

    order.total_amount = total
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

# Initialize database with dummy data
def init_db():
    with app.app_context():
        db.create_all()
        ensure_schema()
        if not Category.query.first():
            makeup = Category(name='Makeup')
            clothes = Category(name='Clothes')
            db.session.add_all([makeup, clothes])
            db.session.commit()

            # Clothes products
            products = [
                Product(
                    name='Silk Evening Dress',
                    description='An elegant silk evening dress with a flattering silhouette. Perfect for special occasions.',
                    price=129.00,
                    image_url='https://images.unsplash.com/photo-1595777457583-95e059d581b8?w=400&h=530&fit=crop',
                    category_id=clothes.id,
                    stock_count=20
                ),
                Product(
                    name='Cashmere Overcoat',
                    description='Luxurious cashmere overcoat in classic beige. Timeless warmth meets modern elegance.',
                    price=245.00,
                    image_url='https://images.unsplash.com/photo-1539533113208-f6df8cc8b543?w=400&h=530&fit=crop',
                    category_id=clothes.id,
                    stock_count=15
                ),
                Product(
                    name='Floral Summer Dress',
                    description='Light and breezy floral dress, perfect for sunny days and garden parties.',
                    price=85.00,
                    image_url='https://images.unsplash.com/photo-1572804013309-59a88b7e92f1?w=400&h=530&fit=crop',
                    category_id=clothes.id,
                    stock_count=25
                ),
                # Makeup products
                Product(
                    name='Velvet Matte Lipstick',
                    description='Long-lasting velvet matte finish in classic red. Bold color that stays all day.',
                    price=32.00,
                    image_url='https://images.unsplash.com/photo-1586495777744-4413f21062fa?w=400&h=530&fit=crop',
                    category_id=makeup.id,
                    stock_count=40
                ),
                Product(
                    name='Luminous Foundation',
                    description='Lightweight liquid foundation with buildable coverage and a natural dewy finish.',
                    price=48.00,
                    image_url='https://images.unsplash.com/photo-1631214524020-7e18db9a8f92?w=400&h=530&fit=crop',
                    category_id=makeup.id,
                    stock_count=30
                ),
                Product(
                    name='Eyeshadow Palette',
                    description='12-shade palette with matte and shimmer finishes. Create endless looks from day to night.',
                    price=55.00,
                    image_url='https://images.unsplash.com/photo-1512496015851-a90fb38ba796?w=400&h=530&fit=crop',
                    category_id=makeup.id,
                    stock_count=18
                ),
            ]
            db.session.add_all(products)
            db.session.commit()


with app.app_context():
    db.create_all()
    ensure_schema()


if __name__ == '__main__':
    init_db()
    app.run(debug=True)
