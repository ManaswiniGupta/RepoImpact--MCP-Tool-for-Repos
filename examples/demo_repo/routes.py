from app import app
from auth import login
from payments import checkout


@app.post("/login")
def login_endpoint(username, password):
    return login(username, password)


@app.post("/checkout")
def checkout_endpoint(order_id, amount):
    return checkout(order_id, amount)
