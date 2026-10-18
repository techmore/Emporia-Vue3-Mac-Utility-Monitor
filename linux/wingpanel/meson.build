project('energy-monitor-wingpanel', 'vala', 'c', version: '0.1.0', meson_version: '>=0.59.0')
wingpanel = dependency('wingpanel-9')
gnome = import('gnome')
theme = gnome.compile_resources('energy-theme', 'theme.gresource.xml', source_dir: '..', c_name: 'energy_theme')
shared_module('energy-monitor', 'Indicator.vala', 'HousePreview.vala', theme,
    dependencies: [wingpanel, dependency('granite-7'), dependency('json-glib-1.0')],
    install: true, install_dir: wingpanel.get_variable(pkgconfig: 'indicatorsdir'))
