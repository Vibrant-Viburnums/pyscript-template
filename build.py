from __future__ import annotations

from typing import NamedTuple, TYPE_CHECKING
from argparse import ArgumentParser
from pathlib import Path
from posixpath import normpath, join as posix_join
from string import Template
from zipfile import ZipFile
from io import BytesIO
from tomllib import loads as toml_loads
from json import dumps
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
import sys
import os

if TYPE_CHECKING:
    from watchdog import events as fsevents
    from watchdog import observers as fsobservers

try:
    import watchdog
    from watchdog import events as fsevents
    from watchdog import observers as fsobservers
except ImportError:
    watchdog = None


class _FileField(NamedTuple):
    content: str
    path: Path


class ZipBuilder:
    def __init__(self) -> None:
        self.files: dict[Path, _FileField] = {}
        self.texts: dict[Path, str] = {}
    
    def add_file(self, file: Path | str, dest: Path | str):
        file = Path(file)

        if file in self.texts:
            del self.texts[file]

        with open(file, "r", encoding="utf-8") as buff:
            self.files[Path(dest)] = _FileField(buff.read(), file)
    
    def add_text(self, text: str, dest: Path | str):
        file = Path(dest)

        if file in self.files:
            del self.texts[file]
        
        self.texts[file] = text
    
    def del_entry(self, path: Path | str):
        path = Path(path)

        if path in self.files: del self.files[path]
        if path in self.texts: del self.texts[path]
    
    def get(self, path: Path | str) -> str:
        path = Path(path)
        
        if path in self.files:
            file = self.files[path]
            return file.content
        
        if path in self.texts:
            return self.texts[path]
        
        raise FileNotFoundError(f"no such file in the ZIP: {path}")
    
    def has(self, path: Path | str):
        path = Path(path)
        return path in self.files or path in self.texts

    def build_zip(self):
        buffer = BytesIO()
        zip = ZipFile(buffer, "w")

        for path in self.files:
            zip.writestr(str(path), self.get(path))
        
        for path in self.texts:
            zip.writestr(str(path), self.get(path))
        
        zip.close()
        try:
            return buffer.getvalue()
        finally:
            buffer.close()


CDN = Template("https://pyscript.net/releases/$version")
INDEX_TEMPLATE = "index.html.template"
INDEX_LOC = "index.html"
PYSCRIPT_TOML_CFG_TEMPLATE = "pyscript.toml.template"
PYSCRIPT_JSON_CFG_TEMPLATE = "pyscript.json.template"
HOST_AT = ("localhost", 8000)


class Project:
    def __init__(self, config: Path, zip: ZipBuilder) -> None:
        self.config = toml_loads(config.read_text())
        self.rel_dir = config.absolute().resolve().parent
        self.zip = zip
        self.files: list[Path] = []
        self.pyscript_config = ""
        self.src_path = Path(self.rel_dir / self.config["project"]["src"]).resolve()
        self.cfg_dir = Path(self.rel_dir / self.config["project"]["config"]).resolve()
        self.toml_cfg = (self.cfg_dir / PYSCRIPT_TOML_CFG_TEMPLATE).resolve()
        self.json_cfg = (self.cfg_dir / PYSCRIPT_JSON_CFG_TEMPLATE).resolve()
        self.index_template = (self.cfg_dir / INDEX_TEMPLATE).resolve()
    
    def convert_path(self, path: Path | str):
        path = Path(path)

        if path.is_absolute():
            if not path.is_relative_to(self.src_path):
                return
            dest_path =  path.relative_to(self.src_path)
        elif path.is_relative_to(self.config["project"]["src"]):
            dest_path = path.relative_to(self.config["project"]["src"])
        else:
            dest_path = path
        return dest_path
    
    def _handle_unknwon(self, path: Path | str):
        path = Path(path)
        if path == self.toml_cfg or path == self.json_cfg:
            self.zip.add_text(self._parse_pyscript_cfg(), self.pyscript_config)
        elif path == self.index_template:
            self.zip.add_text(self._parse_index_html(), INDEX_LOC)
    
    def add_file(self, path: Path | str):
        dest_path = self.convert_path(path)
        if not dest_path: return self._handle_unknwon(path)
        
        if not self.zip.has(dest_path):
            self.files.append(dest_path)
            self._parse_pyscript_cfg()
        self.zip.add_file(path, dest_path)
    
    def del_file(self, path: Path | str):
        dest_path = self.convert_path(path)
        if not dest_path: return
        if not self.zip.has(dest_path): return
        self.files.remove(dest_path)
        self.zip.del_entry(dest_path)
        self._parse_pyscript_cfg()

    def add_src(self):
        if not self.config["runtime"]["remote_cdn"]:
            raise NotImplementedError("currently only remote cdn is supported")
        
        for pth in self.src_path.rglob("*"):
            if not pth.is_file(): continue
            self.add_file(pth)
    
    def _gen_file_entry(self, toml: bool):
        if toml:
            return "\n" + "\n".join([f"{dumps(str(pth))} = ''" for pth in self.files])
        # Json format:
        return "\n" + "\n".join([f"{dumps(str(pth))}: ''," for pth in self.files])
    
    def _gen_cfg_replace(self, toml: bool):
        return {
            "files_entry": self._gen_file_entry(toml)
        }
    
    def _parse_pyscript_cfg(self):
        if self.toml_cfg.exists() and self.toml_cfg.is_file():
            self.pyscript_config = "pyscript.toml"
            return Template(self.toml_cfg.read_text("utf-8")).substitute(self._gen_cfg_replace(True))
        elif self.json_cfg.exists() and self.json_cfg.is_file():
            self.pyscript_config = "pyscipt.json"
            return Template(self.json_cfg.read_text("utf-8")).substitute(self._gen_cfg_replace(False))

        raise ValueError("non-existant pyscript config")
    
    def _parse_index_html(self):
        index_template = self.index_template.read_text("utf-8")
        return Template(index_template).substitute(
            {
                "cdn": CDN.substitute(version=self.config["runtime"]["pyscript"]),
                "script_type": self.config["runtime"]["script_type"],
                "main_script": self.config["project"]["main"],
                "pyscript_config": self.pyscript_config,
                "extra_script_params": ""
            }
        )
    
    def add_templates(self):
        self.files.extend((Path(self.pyscript_config), Path(INDEX_LOC)))
        self.zip.add_text(self._parse_pyscript_cfg(), self.pyscript_config)
        self.zip.add_text(self._parse_index_html(), INDEX_LOC)
    
    def serve(self):
        if watchdog is None:
            print("INFO: watchdog (pip install watchdog) is not installed - running without hot-reloading")
            observer = None
        else:
            updater = ProjectUpdater(self)
            observer = fsobservers.Observer()
            observer.schedule(updater, str(self.rel_dir), recursive=True)
            observer.start()

        with ProjectServer(HOST_AT, project=self) as httpd:
            print(f"Serving on: http://{HOST_AT[0]}:{HOST_AT[1]}")

            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\nKeyboard interrupt received, exiting.")
                if observer is not None:
                    observer.stop()
                    observer.join()
                sys.exit(0)

    def write(self):
        out_path = Path(self.rel_dir / self.config["build"]["out"]).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.touch()
        out_path.write_bytes(self.zip.build_zip())
    
    def get(self, path: Path | str):
        return self.zip.get(path)
    
    def has(self, path: Path | str):
        return self.zip.has(path)


if watchdog is not None:
    class ProjectUpdater(fsevents.LoggingEventHandler):
        def __init__(self, project: Project) -> None:
            super().__init__()
            self.project = project

        def on_created(self, event: fsevents.DirCreatedEvent | fsevents.FileCreatedEvent) -> None:
            super().on_created(event)
            if isinstance(event, fsevents.DirCreatedEvent): return
            self.project.add_file(os.fsdecode(event.src_path))
        
        def on_modified(self, event: fsevents.DirModifiedEvent | fsevents.FileModifiedEvent) -> None:
            super().on_modified(event)
            if isinstance(event, fsevents.DirModifiedEvent): return
            self.project.add_file(os.fsdecode(event.src_path))
        
        def on_deleted(self, event: fsevents.DirDeletedEvent | fsevents.FileDeletedEvent) -> None:
            super().on_deleted(event)
            if isinstance(event, fsevents.DirDeletedEvent): return
            self.project.del_file(os.fsdecode(event.src_path))
        
        def on_moved(self, event: fsevents.DirMovedEvent | fsevents.FileMovedEvent) -> None:
            super().on_moved(event)
            if isinstance(event, fsevents.DirMovedEvent): return
            self.project.del_file(os.fsdecode(event.src_path))
            self.project.add_file(os.fsdecode(event.dest_path))


class ProjectServer(ThreadingHTTPServer):
    def __init__(self, server_address, bind_and_activate: bool = True, *, project: Project) -> None:
        super().__init__(server_address, ProjectServerHandler, bind_and_activate)
        self.project = project
    
    def finish_request(self, request, client_address):
        ProjectServerHandler(request, client_address, self, project=self.project)


class ProjectServerHandler(BaseHTTPRequestHandler):
    INDEX_PAGES = ("index.html", "index.htm")

    def __init__(self, *args, project: Project | None = None, **kwargs):
        self.project = project
        super().__init__(*args, **kwargs)

    def parse_path(self, pth: str):
        if pth.startswith("/"):
            pth = pth[1:]
        
        return normpath(pth)
    
    def guess_mimetype(self, path: str):
        guess, _ = mimetypes.guess_file_type(path)
        if guess: return f"{guess};charset=utf-8"
        return f"application/octet-stream;charset=utf-8"

    def _do_get(self, send_content: bool):
        path = self.parse_path(self.path)

        if self.project is None:
            return self.send_error(HTTPStatus.NOT_FOUND, "File not found.")

        if not self.project.has(path):
            for ext in self.INDEX_PAGES:
                if self.project.has(pth := posix_join(path, ext)):
                    path = pth
                    break
            else:
                return self.send_error(HTTPStatus.NOT_FOUND, "File not found.")
        
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-type", self.guess_mimetype(path))
        content = self.project.get(path)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        if send_content:
            self.wfile.write(content.encode("utf-8"))
    
    def do_GET(self):
        self._do_get(True)
    
    def do_HEAD(self):
        self._do_get(False)


def main():
    parser = ArgumentParser()
    parser.add_argument("config", default="config.toml", nargs="?", type=Path)
    parser.add_argument("--dev", action="store_true")
    args = parser.parse_args()
    zip = ZipBuilder()
    project = Project(args.config, zip)
    project.add_src()
    project.add_templates()

    if args.dev:
        project.serve()
    else:
        project.write()


if __name__ == "__main__":
    main()
