from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, TextAreaField, IntegerField, FloatField, SelectField
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError, NumberRange, Optional
from sqlalchemy import func
from models import User

class RegistrationForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=2, max=20)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Sign Up')

    def validate_username(self, username):
        clean = (username.data or '').strip().lower()
        user = User.query.filter(func.lower(User.username) == clean).first()
        if user:
            raise ValidationError('That username is taken. Please choose a different one.')

    def validate_email(self, email):
        clean = (email.data or '').strip().lower()
        user = User.query.filter(func.lower(User.email) == clean).first()
        if user:
            raise ValidationError('That email is taken. Please choose a different one.')

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

class ReviewForm(FlaskForm):
    rating = IntegerField('Rating (1-5)', validators=[DataRequired()])
    comment = TextAreaField('Comment', validators=[DataRequired()])
    submit = SubmitField('Post Review')


class AddToCartForm(FlaskForm):
    quantity = IntegerField(
        'Quantity',
        default=1,
        validators=[DataRequired(), NumberRange(min=1, max=999)],
    )
    submit = SubmitField('Add to cart')


class CartUpdateForm(FlaskForm):
    quantity = IntegerField('Quantity', validators=[DataRequired(), NumberRange(min=1, max=999)])
    submit = SubmitField('Update')


class RemoveFromCartForm(FlaskForm):
    submit = SubmitField('Remove')


class CheckoutCartForm(FlaskForm):
    delivery_full_name = StringField('Full name', validators=[DataRequired(), Length(max=120)])
    delivery_phone = StringField('Phone', validators=[DataRequired(), Length(max=40)])
    delivery_address = TextAreaField('Street address', validators=[DataRequired(), Length(max=500)])
    delivery_city = StringField('City', validators=[DataRequired(), Length(max=80)])
    delivery_postal = StringField('Postal / ZIP code', validators=[DataRequired(), Length(max=20)])
    delivery_country = StringField('Country', validators=[DataRequired(), Length(max=80)])
    payment_method = SelectField(
        'Payment method',
        choices=[
            ('card', 'Credit / debit card'),
            ('cod', 'Cash on delivery'),
        ],
        validators=[DataRequired()],
    )
    payment_card_holder = StringField('Name on card', validators=[Optional(), Length(max=120)])
    payment_card_last4 = StringField('Card last 4 digits', validators=[Optional(), Length(max=4)])
    guest_email = StringField('Email (guest checkout)', validators=[Optional(), Email()])
    submit = SubmitField('Place order')

    def validate_guest_email(self, field):
        from flask_login import current_user
        if not current_user.is_authenticated:
            if not field.data or not field.data.strip():
                raise ValidationError('Email is required for guest checkout.')

    def validate_payment_card_holder(self, field):
        if self.payment_method.data == 'card':
            if not field.data or not field.data.strip():
                raise ValidationError('Enter the name on the card.')

    def validate_payment_card_last4(self, field):
        if self.payment_method.data == 'card':
            raw = (field.data or '').strip()
            if len(raw) != 4 or not raw.isdigit():
                raise ValidationError('Enter exactly 4 digits (last digits of your card).')


class SellerProductForm(FlaskForm):
    name = StringField('Product Name', validators=[DataRequired(), Length(min=2, max=100)])
    description = TextAreaField('Description', validators=[DataRequired(), Length(min=10)])
    price = FloatField('Price', validators=[DataRequired(), NumberRange(min=0)])
    category_id = SelectField('Category', coerce=int, validators=[DataRequired()])
    stock_count = IntegerField('Stock Count', validators=[DataRequired(), NumberRange(min=0)])
    submit = SubmitField('Save Product')


class OrderStatusForm(FlaskForm):
    status = SelectField(
        'Order Status',
        choices=[
            ('pending', 'Pending'),
            ('paid', 'Paid'),
            ('shipped', 'Shipped'),
            ('delivered', 'Delivered'),
            ('cancelled', 'Cancelled'),
        ],
        validators=[DataRequired()],
    )
    submit = SubmitField('Update Status')


class CheckoutForm(FlaskForm):
    quantity = IntegerField('Quantity', default=1, validators=[DataRequired()])
    delivery_full_name = StringField('Full name', validators=[DataRequired(), Length(max=120)])
    delivery_phone = StringField('Phone', validators=[DataRequired(), Length(max=40)])
    delivery_address = TextAreaField('Street address', validators=[DataRequired(), Length(max=500)])
    delivery_city = StringField('City', validators=[DataRequired(), Length(max=80)])
    delivery_postal = StringField('Postal / ZIP code', validators=[DataRequired(), Length(max=20)])
    delivery_country = StringField('Country', validators=[DataRequired(), Length(max=80)])
    payment_method = SelectField(
        'Payment method',
        choices=[
            ('card', 'Credit / debit card'),
            ('cod', 'Cash on delivery'),
        ],
        validators=[DataRequired()],
    )
    payment_card_holder = StringField('Name on card', validators=[Optional(), Length(max=120)])
    payment_card_last4 = StringField('Card last 4 digits', validators=[Optional(), Length(max=4)])
    guest_email = StringField('Email (guest checkout)', validators=[Optional(), Email()])
    submit = SubmitField('Place order')

    def __init__(self, max_stock=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cap = max_stock if max_stock is not None else 999
        cap = max(1, min(int(cap), 999))
        self.quantity.validators = [
            DataRequired(),
            NumberRange(min=1, max=cap),
        ]

    def validate_guest_email(self, field):
        from flask_login import current_user
        if not current_user.is_authenticated:
            if not field.data or not field.data.strip():
                raise ValidationError('Email is required for guest checkout.')

    def validate_payment_card_holder(self, field):
        if self.payment_method.data == 'card':
            if not field.data or not field.data.strip():
                raise ValidationError('Enter the name on the card.')

    def validate_payment_card_last4(self, field):
        if self.payment_method.data == 'card':
            raw = (field.data or '').strip()
            if len(raw) != 4 or not raw.isdigit():
                raise ValidationError('Enter exactly 4 digits (last digits of your card).')
