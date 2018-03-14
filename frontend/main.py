from flask import Flask, request, render_template
from flask_socketio import SocketIO, emit
from flight_info import FlightInfo
import data_exporter
import os
import sys
import requests
import json
import threading
import datetime
import uuid
import flask
import threading

app = Flask(__name__)
app.config.from_pyfile('config_file.cfg')
data_exporter.initialize_endpoint(app.config['LOGGING_SIDECAR_ENDPOINT'])

socketio = SocketIO(app)

flight_info = FlightInfo()
is_monitoring = False

@app.before_request
def before_request():
    data_exporter.before_flask_request()

@app.after_request
def after_request(response):
    data_exporter.after_flask_request(request, response)
    return response

@app.errorhandler(Exception)
def error_handler(error):
    data_exporter.handle_flask_request_error(error)

@app.route('/', methods=['GET', 'POST'])
def index():
    global is_monitoring
    if request.method == 'GET':
        return render_template("index.html", flight_info = flight_info, is_monitoring = is_monitoring )
    elif request.method == 'POST':
        if request.form['vote'] == 'startNewFlight':
            return start_new_flight(request)
        else:
            if not is_monitoring:
                is_monitoring = True
                show_flights()
            elif is_monitoring:
                is_monitoring = False

            return render_template("index.html", feedback = "Flights monitoring started" if is_monitoring else "Flights monitoring stopped", 
                flight_info = flight_info, is_monitoring = is_monitoring)

def start_new_flight(request):
    thread_local = threading.local()
    thread_local.activity_id = flask.g.activity_id

    flight_info.departure = request.form['departure']
    flight_info.destination = request.form['destination']
    flight_info.callsign = request.form['callsign']
    if (not flight_info.departure or not flight_info.destination or not flight_info.callsign):
        return render_template("index.html", feedback = "Input can't be null!", flight_info = flight_info, is_monitoring = is_monitoring)

    session = requests.Session()
    data = dict(
        DeparturePoint = dict(Name = flight_info.departure),
        Destination = dict(Name = flight_info.destination),
        CallSign = flight_info.callsign
    )
    headers = {'Content-Type': 'application/json'}
    req = requests.Request(method = 'PUT', url = app.config['ATCSERVICE_ENDPOINT'], data = json.dumps(data),
        headers = headers, hooks = {'response': hook_factory(thread_local)})
    prepped = session.prepare_request(req)
    data_exporter.before_http_request(prepped, thread_local)

    try:
        response = session.send(prepped)
        if (response.status_code < 300):
            return render_template("index.html", feedback = f"New flight started: {response.status_code}", flight_info = flight_info, is_monitoring = is_monitoring)
        else:
            return render_template("index.html", feedback = f"Failed to start flight, {response.status_code}, {response.reason}", flight_info = flight_info, is_monitoring = is_monitoring)
    except Exception as e:
        data_exporter.handle_http_request_exception(e)

def show_flights():
    thread = threading.Thread(target = start_monitoring, args = (flask.g.activity_id,))
    thread.start()

def start_monitoring(activity_id):
    try:
        global is_monitoring
        thread_local = threading.local()
        thread_local.activity_id = activity_id

        session = requests.Session()
        req = requests.Request(method = 'GET', url = app.config['ATCSERVICE_ENDPOINT'])
        prepped = session.prepare_request(req)
        data_exporter.before_http_request(prepped, thread_local)

        # TODO: check how AI handle the stream request, here we track the dependency and don't wait for the response
        data_exporter.track_http_stream_request(prepped, thread_local)

        response = session.send(prepped, stream = True)
        for line in response.iter_lines():
            if not is_monitoring:
                response.close()
                socketio.emit("newevent", {'message': f'{datetime.datetime.now()}: Monitoring is stopped on request'})
                return
            if line: # filter out keep-alive new lines
                socketio.emit("newevent", {'message': f'{datetime.datetime.now()}: {line}'})
    except Exception as e:
        data_exporter.handle_http_request_exception(e)
        is_monitoring = False
        socketio.emit("newevent", {'message': f'{datetime.datetime.now()}: Stopping monitoring flights, this could be a bug of python requests library. Click "Show flights" button to start monitoring again.'})

def hook_factory(thread_local):
    def response_hook(response, *request_args, **request_kwargs):
        # There is a weird bug that if I set a breakpoint at here, it will throw runtime error saying responsed content is consumed
        data_exporter.after_http_request(response, thread_local)
        return response

    return response_hook

if __name__ == "__main__":
    socketio.run(app)