import uuid
import flask
import json
import requests
import datetime
import threading

def initialize_endpoint(endpoint):
    global http_endpoint
    http_endpoint = endpoint

# Id looks like '|a000b421-5d183ab6-Server1.1.8e2d4c28_1.'
#  - '|a000b421-5d183ab6-Server1.' - Id of the first, top-most, Activity created
#  - '|a000b421-5d183ab6-Server1.1.' - Id of a child activity. It was started in the same process as the first activity and ends with '.'<para />
#  - '|a000b421-5d183ab6-Server1.1.8e2d4c28_' - Id of the grand child activity. It was started in another process and ends with '_'
class ActivityId:
    id = ""
    parent_id = ""
    current_sequence = 0

    def __init__(self, parent_id = ""):
        if not parent_id:
            self.id = self.__generate_root_id()
            return

        self.parent_id = parent_id
        self.__generate_id()

    def get_root_id(self):
        end_index = self.id.find(".")
        start_index = 1 if self.id[0] == '|' else 0
        return self.id[start_index : end_index]

    def generate_child_id(self):
        child_id = ActivityId()
        child_id.parent_id = self.id
        child_id.id = self.id + str(self.current_sequence) + "."
        self.current_sequence += 1

        return child_id

    def __generate_root_id(self):
        return f'|{uuid.uuid4()}.'

    def __generate_id(self):
        # shouldn't change the original parent id, but when create new id, do sanitization
        sanitized_parent_id = self.parent_id if self.parent_id[0] == "|" else "|" + self.parent_id
        if sanitized_parent_id[-1] != '.' and sanitized_parent_id[-1] != '_':
            sanitized_parent_id += '.'

        self.id = sanitized_parent_id + str(uuid.uuid4())[0:8] + "_"

def before_flask_request():
    requestId = flask.request.headers.get("Request-Id")
    activity_id = ActivityId(requestId)
    flask.g.activity_id = activity_id
    flask.g.start_time = datetime.datetime.utcnow()
    # TODO: Setting Request-Id in the header will enable correlation related features in AppInsights if all telemetries are sent to one AI component.
    # However, if the telemetries are sent to different AI components, setting only "Request-Id" is not enough, becasue AI needs to discover all related components from a starting component.
    # To make the data exporter work for multiple components scenario, it needs to first query the appId of the AI component given the instrumentation key (which is not good since
    # we want the application to be unware of the AI configuration), then set "appId" in the request/response header.


def after_flask_request(request, response):
    activity_id = flask.g.activity_id
    start_time = flask.g.start_time
    end_time = datetime.datetime.utcnow()
    operation_name = f"{request.method} {request.endpoint}"
    tags = {
        "ai.operation.id": activity_id.get_root_id(),
        "ai.operation.parentId": activity_id.parent_id,
        "ai.operation.name": operation_name
    }

    data = {
        "baseType": "RequestData",
        "baseData": {
            "id": activity_id.id,
            "name": operation_name,
            "duration": time_diff_to_timespan(end_time - start_time),
            "success": True if response.status_code < 400 else False,
            "responseCode": response.status_code,
            "url": request.base_url,
            "properties": {
                "httpMethod": request.method
            }
        }
    }

    request_telemetry = {
        "name": "Microsoft.ApplicationInsights.Request",
        "time": end_time.isoformat(),
        "tags": tags,
        "data": data
    }

    try:
        requests.post(http_endpoint, data=json.dumps(request_telemetry), headers = {'Content-Type': 'application/json'})
    finally:
        return

def handle_flask_request_error(error):
    # TODO: track exception and request failure
    return

# TODO: flask.g is based on request context, and doesn't work in multi-threading cases.
# We need something like AsyncLocal, like Execution Context which is still in proposal: https://www.python.org/dev/peps/pep-0550/
# We're currently using thread_local storage as a hack, but it's not working very well. It's like CallContext.SetDate() in C#, while what we need is CallContext.LogicalSetData().
# More explicitly, the data will not be passed down the stream when a new thread is created, thus we will have to pass the information needed when creating the new thread,
# see how the activity_id and operation_name are passed in start_monitoring() and set on thread_local again
#
# A dependency map is also used in the thread_local. It would be nice if we can simply store the info in the request context, like flask.g,
# and retrieve it in the response.
thread_local = threading.local()

# TODO: the python requests library has the Event Hook functionality. However, currently there are two limitations
# 1) There is no global hook, and user need to manually add the hook for every request 
# 2) The only hook available now is response, so before each request, user need to call some method to inject request id to the request header but not hook.
#    Actually in earlier versions (0.7.3 for exampke) there is pre_request hook, not idea why it's removed.
def before_http_request(request):
    if not hasattr(thread_local, 'dependency_activity_id_map'):
        thread_local.dependency_activity_id_map = dict()

    if not hasattr(thread_local, 'dependency_start_time_map'):
        thread_local.dependency_start_time_map = dict()

    activity_id = thread_local.activity_id.generate_child_id()
    request.headers["Request-Id"] = activity_id.id
    thread_local.dependency_activity_id_map[activity_id.id] = activity_id
    thread_local.dependency_start_time_map[activity_id.id] = datetime.datetime.utcnow()


def after_http_request(response, *args, **kwargs):
    # Assume they all exist
    request = response.request
    request_id = request.headers.get("Request-Id")
    activity_id = thread_local.dependency_activity_id_map.get(request_id)
    start_time = thread_local.dependency_start_time_map.get(request_id)
    end_time = datetime.datetime.utcnow()

    tags = {
        "ai.operation.id": activity_id.get_root_id(),
        "ai.operation.parentId": activity_id.parent_id,
        # This is the incoming request's operation name, which is different from the dependency telemetry's name
        "ai.operation.name": thread_local.operation_name
    }

    data = {
        "baseType": "RemoteDependencyData",
        "baseData": {
            "id": activity_id.id,
            "name": f"{request.method} {request.path_url}",
            "duration": time_diff_to_timespan(end_time - start_time),
            "success": True if response.status_code < 400 else False,
            "resultCode": response.status_code,
            "type": "HTTP",
            "data": request.url,
            "target": request.url[0: len(request.url) - len(request.path_url)]
        }
    }

    request_telemetry = {
        "name": "Microsoft.ApplicationInsights.RemoteDependency",
        "time": end_time.isoformat(),
        "tags": tags,
        "data": data
    }

    try:
        requests.post(http_endpoint, data=json.dumps(request_telemetry), headers = {'Content-Type': 'application/json'})
    except Exception:
        pass

def track_http_stream_request(request):
    request_id = request.headers.get("Request-Id")
    activity_id = thread_local.dependency_activity_id_map.get(request_id)
    end_time = datetime.datetime.utcnow()

    tags = {
        "ai.operation.id": activity_id.get_root_id(),
        "ai.operation.parentId": activity_id.parent_id,
        "ai.operation.name": thread_local.operation_name
    }

    data = {
        "baseType": "RemoteDependencyData",
        "baseData": {
            "id": activity_id.id,
            "name": f"{request.method} {request.path_url}",
            "duration": "00:00:00",
            "success": True,
            "resultCode": 200,
            "type": "HTTP",
            "data": request.url,
            "target": request.url[0: len(request.url) - len(request.path_url)]
        }
    }

    request_telemetry = {
        "name": "Microsoft.ApplicationInsights.RemoteDependency",
        "time": end_time.isoformat(),
        "tags": tags,
        "data": data
    }

    try:
        requests.post(http_endpoint, data=json.dumps(request_telemetry), headers = {'Content-Type': 'application/json'})
    finally:
        return

def handle_http_request_exception(exception):
    # TODO: track exception and dependency failure
    return

def time_diff_to_ms(diff):
    return (diff.days * 86400000) + (diff.seconds * 1000) + (diff.microseconds / 1000)

# Convert the time diff to C# Timespan format: dd.hh:mm:ss.fffffff
def time_diff_to_timespan(diff):
    hours, remainder = divmod(diff.seconds, 60 * 60)
    mins, seconds = divmod(remainder, 60)
    timespan = "{hh:02d}:{mm:02d}:{ss:02d}.{ticks:07d}".format(hh=hours, mm=mins, ss=seconds, ticks=diff.microseconds*10)
    timespan = "{dd:02d}".format(dd=diff.days) + "." + timespan if diff.days > 0 else timespan

    return timespan