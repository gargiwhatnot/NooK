from rembg import remove, new_session
from PIL import Image
from io import BytesIO
from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
import os
import uuid
import random
import json

app = Flask(__name__)

# SESSION CONFIGURATION
app.config["SECRET_KEY"] = "nook-development-secret-key"
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 30
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

# DATABASE CONFIGURATION
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///nook.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Initialize background removal model once
rembg_session = new_session("u2netp")

# IMAGE UPLOAD CONFIGURATION
UPLOAD_FOLDER = "static/uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

db = SQLAlchemy(app)


# CLOTHING DATABASE TABLE
class Clothing(db.Model):

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    colour = db.Column(db.String(50), nullable=False)
    occasion = db.Column(db.String(50))

    image = db.Column(db.String(200), nullable=False)

    guest_id = db.Column(db.String(100), nullable=True)
    user_id = db.Column(db.Integer, nullable=True)

# CREATE GUEST SESSION
@app.before_request
def create_guest_session():

    if "guest_id" not in session:

        session["guest_id"] = str(uuid.uuid4())

    session.permanent = True

    # Assign existing unowned clothing to the first visitor
    if not session.get("legacy_clothes_assigned"):

        old_clothes = Clothing.query.filter_by(
            guest_id=None,
            user_id=None
        ).all()

        for item in old_clothes:
            item.guest_id = session["guest_id"]

        db.session.commit()

        session["legacy_clothes_assigned"] = True


# HOMEPAGE
@app.route("/")
def home():
    return render_template("index.html")


# DASHBOARD
@app.route("/dashboard")
def dashboard():

    guest_id = session["guest_id"]

    total_items = Clothing.query.filter_by(
        guest_id=guest_id
    ).count()

    return render_template(
        "dashboard.html",
        total_items=total_items
    )


# WARDROBE
@app.route("/wardrobe")
def wardrobe():

    guest_id = session["guest_id"]

    clothes = Clothing.query.filter_by(
        guest_id=guest_id
    ).order_by(Clothing.id.desc()).all()

    total_items = len(clothes)

    total_tops = Clothing.query.filter_by(
        guest_id=guest_id,
        category="Tops"
    ).count()

    total_bottoms = Clothing.query.filter_by(
        guest_id=guest_id,
        category="Bottoms"
    ).count()

    total_shoes = Clothing.query.filter_by(
        guest_id=guest_id,
        category="Shoes"
    ).count()

    return render_template(
        "wardrobe.html",
        clothes=clothes,
        total_items=total_items,
        total_tops=total_tops,
        total_bottoms=total_bottoms,
        total_shoes=total_shoes
    )

def process_clothing_image(image_bytes):

    # Open original image
    image = Image.open(BytesIO(image_bytes)).convert("RGB")

    # Resize large images before AI processing
    image.thumbnail((1000, 1000), Image.Resampling.LANCZOS)

    # Convert resized image to bytes
    input_buffer = BytesIO()
    image.save(input_buffer, format="PNG")

    # Remove background using U2NETP
    processed_bytes = remove(
        input_buffer.getvalue(),
        session=rembg_session
    )

    # Open transparent processed image
    image = Image.open(BytesIO(processed_bytes)).convert("RGBA")

    # Crop transparent space around clothing
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()

    if bbox:
        image = image.crop(bbox)

    # Resize clothing while preserving proportions
    image.thumbnail((420, 520), Image.Resampling.LANCZOS)

    # Standard transparent canvas
    canvas = Image.new(
        "RGBA",
        (500, 600),
        (0, 0, 0, 0)
    )

    # Center clothing
    x = (500 - image.width) // 2
    y = (600 - image.height) // 2

    canvas.alpha_composite(image, (x, y))

    # Save processed image as PNG in memory
    output = BytesIO()
    canvas.save(output, format="PNG")

    return output.getvalue()

# UPLOAD CLOTHES
@app.route("/upload", methods=["GET", "POST"])
def upload():

    if request.method == "POST":

        name = request.form.get("name")
        category = request.form.get("category")
        colour = request.form.get("colour")
        occasion = request.form.get("occasion")

        file = request.files.get("image")

        if not file or not file.filename:
            return "Please upload an image", 400

        try:

            # Read original image into memory
            original_bytes = file.read()

            # Validate that it is a readable image
            Image.open(BytesIO(original_bytes)).verify()

            # Process image
            processed_bytes = process_clothing_image(original_bytes)

            # Generate unique filename
            filename = str(uuid.uuid4()) + ".png"

            # Save only the processed image
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)

            with open(filepath, "wb") as image_file:
                image_file.write(processed_bytes)

            # Save clothing information in database
            new_clothing = Clothing(
                name=name,
                category=category,
                colour=colour,
                occasion=occasion,
                image=filename,
                guest_id=session["guest_id"]
            )

            db.session.add(new_clothing)
            db.session.commit()

            return redirect(url_for("wardrobe"))

        except Exception as error:

            print("Image processing error:", error)

            return "Image processing failed. Please try another image.", 400

    return render_template("upload.html")

@app.route("/delete-clothing/<int:item_id>", methods=["POST"])
def delete_clothing(item_id):

    # Find clothing belonging to the current guest
    item = Clothing.query.filter_by(
        id=item_id,
        guest_id=session["guest_id"]
    ).first_or_404()

    # Get image path
    image_path = os.path.join(
        app.config["UPLOAD_FOLDER"],
        item.image
    )

    # Delete image file
    if os.path.exists(image_path):
        os.remove(image_path)

    # Delete database record
    db.session.delete(item)
    db.session.commit()

    return redirect(url_for("wardrobe"))

@app.route("/about")
def about():
    return render_template("about.html")

# CREATE DATABASE
with app.app_context():

    db.create_all()

    # Add guest_id column to existing database if necessary
    with db.engine.connect() as connection:

        columns = connection.exec_driver_sql(
            "PRAGMA table_info(clothing)"
        ).fetchall()

        column_names = [column[1] for column in columns]

        if "guest_id" not in column_names:

            connection.exec_driver_sql(
                "ALTER TABLE clothing ADD COLUMN guest_id VARCHAR(100)"
            )

        if "user_id" not in column_names:

            connection.exec_driver_sql(
                "ALTER TABLE clothing ADD COLUMN user_id INTEGER"
            )

        connection.commit()


if __name__ == "__main__":
    app.run(debug=True)