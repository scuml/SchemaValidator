import sublime, sublime_plugin
import threading
import urllib.request
import json
import os, sys
import re

sys.path.append(os.path.dirname(os.path.realpath(__file__)))
import jsonschema


class OnSaveHandler(sublime_plugin.EventListener):

    def on_post_save(self, view):
        # Only run on json files
        if "JSON" in view.settings().get('syntax'):
            view.run_command('validate_schema')


class Loading():
    def __init__(self, view, status_message, display_message, callback):
        self.view = view
        self.i = 0
        self.dir = 1
        self.status_message = status_message
        self.display_message = display_message
        self.callback = callback
    def increment(self):
        before = self.i % 8
        after = (7) - before
        if not after:
            self.dir = -1
        if not before:
            self.dir = 1
        self.i += self.dir
        self.view.set_status(self.status_message, " [%s=%s]" % \
                (" " * before, " " * after))
        sublime.set_timeout(lambda: self.callback(), 100)
    def clear(self):
        self.view.erase_status(self.status_message)
        pass

class ValidateSchemaCommand(sublime_plugin.TextCommand):
    def run(self, edit, immediate=True):
        self.view.erase_status('schema_validator_status')
        self.thread = ValidateSchema(self.view)
        self.thread.start()
        self.loading = Loading(self.view, "match_schema", "Matching File to schema", self.handle_thread)
        self.handle_thread()

    def handle_thread(self):
        if self.thread.is_alive():
            self.loading.increment()
            return
        self.loading.clear()
        if len(self.thread.errors) > 0:
            display_errors = [[e[0], "Line: {}".format(e[1]['row'])] for e in self.thread.errors]
            self.view.window().show_quick_panel(
                display_errors, self._jump, 0, 0, self._jump)
        else:
            self.view.set_status('schema_validator_status', self.thread.message)
        return

    def _jump(self, item):
        """
        Jump to a line in the view buffer
        """

        if item == -1:
            return

        error, position = self.thread.errors[item]

        col = position.get('col', 1) - 1
        self.view.sel().clear()

        if type(position['row']) in (list, tuple):
            lineno = position['row'][0] - 1
            endlineno = position['row'][1]
            pt = self.view.text_point(lineno, 0)
            endpt = self.view.text_point(endlineno, 0)
            self.view.sel().add(sublime.Region(pt, endpt))
        else:
            lineno = position['row'] - 1

            pt = self.view.text_point(lineno, col)
            self.view.sel().add(sublime.Region(pt))

        self.view.show(pt)

class ValidateSchema(threading.Thread):
    def __init__(self,view):
        self.message = None
        self.errors = []
        self.view = view
        threading.Thread.__init__(self)

    def run(self):

        self.errors = []
        self.raw_data = self.view.substr(sublime.Region(0, self.view.size()))

        # Check for valid JSON
        try:
            json_data = json.loads(self.raw_data)
        except ValueError as e:
            self.errors.append(("Not valid JSON file", {'row': 0}))
            return
        # Check for schema in document
        try:
            schema_url = json_data['$schema']
            self.message = schema_url

        # If no schema attribute was found, let's try a file match
        except (KeyError, TypeError) as e:
            try:
                request = urllib.request.Request("http://schemastore.org/api/json/catalog.json", headers={"User-Agent": "Sublime"})
                http_file = urllib.request.urlopen(request, timeout=5)
                http_response = http_file.read().decode("utf-8")
                try:
                    catalog = json.loads(http_response)["schemas"]
                except ValueError as e:
                    self.errors.append(("Retrieved schema is not a valid JSON file", {'row': 0}))

                    return
                except LookupError as e:
                    self.errors.append(("Catalog.json contains no schemas", {'row': 0}))

            except (urllib.request.HTTPError) as e:
                self.errors.append(("%s: HTTP error %s contacting API" % (__name__, str(e.code)), {'row': 0}))

                return
            except (urllib.request.URLError) as e:
                self.errors.append(("%s: URL error %s contacting API" % (__name__, str(e.reason)), {'row': 0}))
                return
            try:
                file_name = self.view.file_name()[self.view.window().folders()[0].__len__()+1:]
            except IndexError as e:
                file_name = self.view.file_name()
                if file_name == "":
                    self.message = "Try adding a $schema attribute to your file or try saving your file"
            schema_matched = False
            for schema_type in catalog:
                try:
                    for file_match in schema_type["fileMatch"]:
                        # Escape the fileMatch and perform a regex search
                         if(re.compile(file_match.replace("/","\/").replace(".","\.").replace("*",".*")).match(file_name)):
                            schema_url = schema_type['url']
                            schema_matched = True
                            break
                # Some schemas don't have a fileMatch attribute
                except LookupError as e:
                    pass
            if schema_matched == False:
                self.errors.append(("No schema could be matched. Try adding a $schema attribute", {'row': 0}))

                return
        # Use schema_url to retrieve schema
        try:
            request = urllib.request.Request(schema_url, headers={"User-Agent": "Sublime"})
            http_file = urllib.request.urlopen(request, timeout=5)
            http_response = http_file.read().decode('utf-8')
            try:
                schema = json.loads(http_response)
            except ValueError as e:
                self.errors.append(("Retrieved schema is not a valid JSON file", {'row': 0}))

                return
        except (urllib.request.HTTPError) as e:
            self.errors.append(("%s: HTTP error %s contacting API" % (__name__, str(e.code)), {'row': 0}))
            return
        except (urllib.request.URLError) as e:
            self.errors.append(("%s: URL error %s contacting API" % (__name__, str(e.reason)), {'row': 0}))
            return
        try:
            jsonschema.validate(json_data, schema)
        except jsonschema.exceptions.ValidationError as e:
            error_line = self._get_line(e.path)
            self.errors.append((e.message, {'row': error_line}))
            return
        except jsonschema.exceptions.SchemaError as e:
            error_line = self._get_line(e.path)
            self.errors.append((e.message, {'row': error_line}))
            return
        self.message = "JSON Schema successfully validated against %s" % schema_url
        return

    def _get_line(self, path):
        """
        Attempts to get the line of the property
        :param path: list or deque
        :return: int
        """

        match = re.search("^.*?{}".format(".*?".join(path)), self.raw_data, re.S | re.M)
        return match.group().count("\n")
