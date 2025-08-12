# Your pyscript project.
## File structure
```
config/  # config files are located here
  index.html.template  # template file for the index.html
  pyscript.toml.template  # pyscript config file
src/
  # your code and resources go here
build.py  # server / build system code
config.toml  # configure your project
```
## Usage
To build a zip file for itch.io run:
```sh
python build.py
```
Or, if you have a custom config file / multiple projects:
```sh
python path/to/your/custom_config.toml
```
To run a simple development server, add the ``--dev`` flag. Artefacts won't be generated when using this option.

If you want to have hot reloading when running the dev server, you need to install an optional dependency (watchdog):
```sh
pip install watchdog
```
