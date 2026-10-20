# One version source: the repository's VERSION file (scripts/check_release.py keeps it in step with the changelog and metainfo).
project('energy-monitor-wingpanel', 'vala', 'c',
    version: run_command('cat', '../../VERSION', check: true).stdout().strip(),
    meson_version: '>=0.59.0')
# valac's generated C leaves temporaries and array-length locals behind; those two GCC categories carry no
# signal for Vala sources. Everything else (nullability, deprecations, pointer types, qualifiers) stays on.
add_project_arguments('-Wno-unused-but-set-variable', '-Wno-unused-variable', language: 'c')
# GLib 2.80 type registration (g_once_init_enter_pointer): the older form trips GCC 14's volatile check inside GLib's own headers.
add_project_arguments('--target-glib=2.80', language: 'vala')
wingpanel = dependency('wingpanel-9')
math = meson.get_compiler('c').find_library('m')
gnome = import('gnome')
theme = gnome.compile_resources('energy-theme', 'theme.gresource.xml', source_dir: ['..', '../../static/house-control'], c_name: 'energy_theme')
shared_module('energy-monitor', 'Indicator.vala', 'Labels.vala', 'HousePreview.vala', 'TemperatureView.vala', 'WebHouseView.vala', theme,
    dependencies: [dependency('webkitgtk-6.0'), math, wingpanel, dependency('granite-7'), dependency('json-glib-1.0')],
    install: true, install_dir: wingpanel.get_variable(pkgconfig: 'indicatorsdir'))
test_deps = [dependency('webkitgtk-6.0'), math, dependency('gtk4'), dependency('granite-7'), dependency('json-glib-1.0')]
test('temperature-view', executable('test-temperature-view', 'test_temperature_view.vala', 'TemperatureView.vala', 'HousePreview.vala', 'WebHouseView.vala', build_by_default: false,
    dependencies: test_deps))
test('labels', executable('test-labels', 'test_labels.vala', 'Labels.vala', 'TemperatureView.vala', 'HousePreview.vala', 'WebHouseView.vala', build_by_default: false,
    dependencies: test_deps))
