### Adding build support

Can you add the LAN support and build script based on the sample-react-python-project? And if there's any gap between the current implementation and the sample implementation, please point it out. Make sure we have a settings json for %APPDATA%/{app_name}/settings.json and allows the user to point to folder associated with data storage in the setting (by default, it will be at %TEMP%/{app_name}).

### Port support 

Can we allow the tool's configuration %APPDATA%\{application_name}\settings.json takes the port numbers for the frontend port and backend port. By default, when we build, we should insert a small step in the run.bat script (regardless of Debug or Release) with the following script run:

(1) checking if settings.json exist or not, if not create an empty json "{}" into settings.json.

(2) read from settings.json to check if we have the field "frontend_port" and "backend_port" if anything missing populated with default values which  are specified under the current project folder's port_mapping.json.


(3) Read from settings.json again and pass port information to start frontend and backends.