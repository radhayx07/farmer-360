from datetime import date
from flask import Flask, flash, redirect, render_template, request, url_for
from dotenv import load_dotenv
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv
load_dotenv()
from config import Config
from algorithms.graph import build_graph
from algorithms.dijkstra import shortest_path
from algorithms.priority_queue import TaskMinHeap
from algorithms.sorting import sort_markets_by_price

app = Flask(__name__)
app.config.from_object(Config)
app.config["SESSION_COOKIE_NAME"] = "farmer_session"


def get_connection():
    return mysql.connector.connect(
        host=app.config["MYSQL_HOST"], port=app.config["MYSQL_PORT"],
        user=app.config["MYSQL_USER"], password=app.config["MYSQL_PASSWORD"],
        database=app.config["MYSQL_DATABASE"]
    )


def query_all(sql, params=()):
    connection = get_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        cursor.close()
        connection.close()


def execute(sql, params=()):
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(sql, params)
        connection.commit()
        return cursor.lastrowid
    finally:
        cursor.close()
        connection.close()


@app.context_processor
def inject_globals():
    return {"today": date.today()}


@app.route("/")
def index():
    try:
        stats = {
            "farms": query_all("SELECT COUNT(*) AS total FROM farms")[0]["total"],
            "crops": query_all("SELECT COUNT(*) AS total FROM crops")[0]["total"],
            "tasks": query_all("SELECT COUNT(*) AS total FROM tasks WHERE status <> 'Done'")[0]["total"],
            "markets": query_all("SELECT COUNT(*) AS total FROM markets")[0]["total"],
        }
        crops = query_all("SELECT c.*, f.name AS farm_name FROM crops c JOIN farms f ON c.farm_id=f.id ORDER BY c.id DESC LIMIT 5")
        markets = sort_markets_by_price(query_all("SELECT * FROM markets ORDER BY price_per_quintal DESC LIMIT 5"))
        tasks = query_all("SELECT * FROM tasks WHERE status <> 'Done' ORDER BY due_date ASC LIMIT 5")
        return render_template("dashboard.html", stats=stats, crops=crops, markets=markets, tasks=tasks)
    except Error as error:
        return render_template("setup_error.html", error=str(error)), 503


@app.route("/farms", methods=["GET", "POST"])
def farms():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        location = request.form.get("location", "").strip()
        area = request.form.get("area_acres", "0").strip()
        soil = request.form.get("soil_type", "Unknown").strip()
        if not name or not location:
            flash("Farm name and location are required.", "error")
        else:
            try:
                execute("INSERT INTO farms (name, location, area_acres, soil_type) VALUES (%s,%s,%s,%s)", (name, location, float(area or 0), soil))
                flash("Farm added successfully.", "success")
                return redirect(url_for("farms"))
            except (Error, ValueError) as error:
                flash(f"Could not save farm: {error}", "error")
    return render_template("farms.html", farms=query_all("SELECT * FROM farms ORDER BY id DESC"))


@app.route("/crops", methods=["GET", "POST"])
def crops():
    if request.method == "POST":
        try:
            execute("INSERT INTO crops (farm_id, name, season, planted_on, health_status, notes) VALUES (%s,%s,%s,%s,%s,%s)", (
                int(request.form["farm_id"]), request.form["name"].strip(), request.form.get("season", "Kharif"),
                request.form.get("planted_on") or None, request.form.get("health_status", "Healthy"), request.form.get("notes", "").strip()))
            flash("Crop added successfully.", "success")
            return redirect(url_for("crops"))
        except (Error, ValueError, KeyError) as error:
            flash(f"Could not save crop: {error}", "error")
    farms_list = query_all("SELECT id, name FROM farms ORDER BY name")
    crop_list = query_all("SELECT c.*, f.name AS farm_name FROM crops c JOIN farms f ON c.farm_id=f.id ORDER BY c.id DESC")
    return render_template("crops.html", farms=farms_list, crops=crop_list)


@app.route("/markets", methods=["GET", "POST"])
def markets():
    if request.method == "POST":
        try:
            execute("INSERT INTO markets (name, location, crop_name, price_per_quintal, updated_on) VALUES (%s,%s,%s,%s,CURDATE())", (
                request.form["name"].strip(), request.form["location"].strip(), request.form["crop_name"].strip(), float(request.form["price_per_quintal"])))
            flash("Market price saved.", "success")
            return redirect(url_for("markets"))
        except (Error, ValueError, KeyError) as error:
            flash(f"Could not save market: {error}", "error")
    all_markets = query_all("SELECT * FROM markets")
    ordered = sort_markets_by_price(all_markets)
    return render_template("markets.html", markets=ordered)


@app.route("/tasks", methods=["GET", "POST"])
def tasks():
    if request.method == "POST":
        try:
            execute("INSERT INTO tasks (title, category, priority, due_date, status, notes) VALUES (%s,%s,%s,%s,'Pending',%s)", (
                request.form["title"].strip(), request.form.get("category", "General"), int(request.form.get("priority", 3)),
                request.form.get("due_date") or None, request.form.get("notes", "").strip()))
            flash("Task added to your priority queue.", "success")
            return redirect(url_for("tasks"))
        except (Error, ValueError, KeyError) as error:
            flash(f"Could not save task: {error}", "error")
    all_tasks = query_all("SELECT * FROM tasks WHERE status <> 'Done'")
    heap = TaskMinHeap()
    for task in all_tasks:
        heap.push(task["priority"], task)
    priority_order = []
    while len(heap):
        priority_order.append(heap.pop())
    return render_template("tasks.html", tasks=priority_order)


@app.post("/tasks/<int:task_id>/done")
def mark_task_done(task_id):
    try:
        execute("UPDATE tasks SET status='Done' WHERE id=%s", (task_id,))
        flash("Task marked as done.", "success")
    except Error as error:
        flash(f"Could not update task: {error}", "error")
    return redirect(url_for("tasks"))


@app.route("/routes", methods=["GET", "POST"])
def routes():
    locations = query_all("SELECT * FROM locations ORDER BY name")
    result = None
    if request.method == "POST":
        try:
            start_id, end_id = int(request.form["start"]), int(request.form["end"])
            roads = query_all("SELECT from_location_id, to_location_id, distance_km FROM roads")
            graph = build_graph([loc["id"] for loc in locations], [(r["from_location_id"], r["to_location_id"], r["distance_km"]) for r in roads])
            path, distance = shortest_path(graph, start_id, end_id)
            names = {loc["id"]: loc["name"] for loc in locations}
            result = {"path": [names[node] for node in path], "distance": round(distance, 2) if path else None,
                      "message": "Shortest route found." if path else "No route connects these locations.",
                      "complexity": "O((V + E) log V) with an adjacency list and min heap"}
        except (Error, ValueError, KeyError) as error:
            flash(f"Could not calculate route: {error}", "error")
    roads = query_all("SELECT r.*, a.name AS from_name, b.name AS to_name FROM roads r JOIN locations a ON r.from_location_id=a.id JOIN locations b ON r.to_location_id=b.id ORDER BY a.name")
    return render_template("routes.html", locations=locations, roads=roads, result=result)


@app.route("/health")
def health():
    try:
        query_all("SELECT 1 AS ok")
        return {"status": "ok", "database": "connected"}
    except Error as error:
        return {"status": "error", "database": str(error)}, 503

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
