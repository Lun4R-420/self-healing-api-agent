import copy
from enum import Enum

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(
    title="Company API",
    version="1.0.0"
)

SEED_USERS = {
    1: {"id": 1, "name": "Alice", "email": "alice@example.com"},
    2: {"id": 2, "name": "Bob", "email": "bob@example.com"},
}

SEED_ORDERS = {
    101: {"id": 101, "user_id": 1, "product": "Keyboard", "quantity": 2},
    102: {"id": 102, "user_id": 1, "product": "Mouse", "quantity": 1},
    103: {"id": 103, "user_id": 2, "product": "Monitor", "quantity": 1},
}

SEED_PRODUCTS = [
    {"id": 1, "name": "Keyboard", "category": "electronics"},
    {"id": 2, "name": "Mouse", "category": "electronics"},
    {"id": 3, "name": "Monitor", "category": "electronics"},
    {"id": 4, "name": "Notebook", "category": "stationery"},
]

users = {}
orders = {}
products = []


def reset_data():
    users.clear()
    users.update(copy.deepcopy(SEED_USERS))
    orders.clear()
    orders.update(copy.deepcopy(SEED_ORDERS))
    products.clear()
    products.extend(copy.deepcopy(SEED_PRODUCTS))


reset_data()


class Category(str, Enum):
    electronics = "electronics"
    stationery = "stationery"


class OrderCreate(BaseModel):
    user_id: int
    product: str
    quantity: int = Field(default=1, gt=0)


class UserUpdate(BaseModel):
    name: str | None = None
    email: str | None = None


@app.get("/users", summary="List users")
def get_users():
    return list(users.values())


@app.get("/users/{user_id}", summary="Get user by id")
def get_user(user_id: int):
    if user_id not in users:
        raise HTTPException(status_code=404, detail="User not found")

    return users[user_id]


@app.patch(
    "/users/{user_id}",
    summary="Update user",
    description="Change a user's name or email."
)
def update_user(user_id: int, update: UserUpdate):
    if user_id not in users:
        raise HTTPException(status_code=404, detail="User not found")

    changes = update.model_dump(exclude_none=True)
    users[user_id].update(changes)

    return users[user_id]


@app.delete(
    "/users/{user_id}",
    summary="Delete user",
    description="Permanently delete a user and all of their orders."
)
def delete_user(user_id: int):
    if user_id not in users:
        raise HTTPException(status_code=404, detail="User not found")

    del users[user_id]

    for order_id in [
        order_id for order_id, order in orders.items()
        if order["user_id"] == user_id
    ]:
        del orders[order_id]

    return {"deleted_user_id": user_id}


@app.get("/users/{user_id}/orders", summary="List orders of a user")
def get_user_orders(user_id: int):
    if user_id not in users:
        raise HTTPException(status_code=404, detail="User not found")

    return [
        order for order in orders.values()
        if order["user_id"] == user_id
    ]


@app.get("/orders/{order_id}", summary="Get order by id")
def get_order(order_id: int):
    if order_id not in orders:
        raise HTTPException(status_code=404, detail="Order not found")

    return orders[order_id]


@app.post(
    "/orders",
    status_code=201,
    summary="Create order",
    description="Place an order for a product on behalf of a user."
)
def create_order(order: OrderCreate):
    if order.user_id not in users:
        raise HTTPException(status_code=404, detail="User not found")

    names = [product["name"] for product in products]

    if order.product not in names:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown product '{order.product}'. "
                f"Available products: {', '.join(names)}"
            )
        )

    order_id = max(orders, default=100) + 1

    orders[order_id] = {
        "id": order_id,
        "user_id": order.user_id,
        "product": order.product,
        "quantity": order.quantity,
    }

    return orders[order_id]


@app.delete(
    "/orders/{order_id}",
    summary="Cancel order",
    description="Permanently delete an order."
)
def delete_order(order_id: int):
    if order_id not in orders:
        raise HTTPException(status_code=404, detail="Order not found")

    del orders[order_id]

    return {"deleted_order_id": order_id}


@app.get("/products", summary="List products in a category")
def get_products(category: Category):
    return [
        product for product in products
        if product["category"] == category.value
    ]
