"""A local OTLP/HTTP trace receiver: decodes exactly what the Neatlogs SDK would send to a hosted project."""
import gzip
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace as NS

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


def value(any_value):
    kind = any_value.WhichOneof("value")
    return getattr(any_value, kind) if kind else None


class Receiver:
    """Collects every span the SDK posts, decoded from OTLP protobuf."""

    def __init__(self):
        self.spans = []
        spans = self.spans

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length", 0)))
                if self.headers.get("content-encoding") == "gzip":
                    body = gzip.decompress(body)
                if self.path.endswith("/v1/traces"):
                    request = ExportTraceServiceRequest()
                    request.ParseFromString(body)
                    for resource in request.resource_spans:
                        resource_attributes = {item.key: value(item.value) for item in resource.resource.attributes}
                        for scope in resource.scope_spans:
                            for span in scope.spans:
                                spans.append(NS(name=span.name, attributes={item.key: value(item.value) for item in span.attributes},
                                    status=span.status.code, status_message=span.status.message, trace_id=span.trace_id.hex(),
                                    span_id=span.span_id.hex(), parent_id=span.parent_span_id.hex(), resource=resource_attributes,
                                    events=[NS(name=event.name, attributes={item.key: value(item.value) for item in event.attributes})
                                            for event in span.events]))
                self.send_response(200)
                self.send_header("content-type", "application/x-protobuf")
                self.send_header("content-length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def named(self, name):
        return [span for span in self.spans if span.name == name]
