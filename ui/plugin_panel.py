"""Plugin management panel — enable/disable plugins and show their custom UI."""
import traceback

import dearpygui.dearpygui as dpg


def create_plugin_panel(plugin_manager, parent: str = "plugin_panel_container"):
    """Build the plugin management section.

    Shows each discovered plugin with name, version, description, enable toggle,
    and custom UI when enabled. Must be called after plugin_manager.load_all().
    """
    with dpg.group(parent=parent):
        discovered = plugin_manager.discover()
        if not discovered:
            dpg.add_text("No plugins found", color=(150, 150, 160))
            return

        for info in discovered:
            name = info["name"]
            version = info.get("version", "0.0.0")
            description = info.get("description", "") or "No description"
            is_loaded = name in plugin_manager.plugins

            container_tag = f"plugin_ui_{name}"
            header_tag = f"plugin_header_{name}"
            checkbox_tag = f"plugin_cb_{name}"

            def make_toggle_callback(plugin_name: str, plugin_info: dict):
                def on_toggle(sender, app_data):
                    mgr = plugin_manager
                    container = f"plugin_ui_{plugin_name}"
                    if app_data:
                        mgr.load_plugin(plugin_info)
                        _rebuild_plugin_ui(mgr, plugin_name, container)
                    else:
                        _clear_plugin_ui(container)
                        mgr.unload_plugin(plugin_name)
                    dpg.configure_item(container, show=app_data)
                return on_toggle

            with dpg.collapsing_header(
                label=f"{name} v{version}",
                tag=header_tag,
                default_open=is_loaded,
            ):
                dpg.add_text(description, color=(150, 150, 160), wrap=400)
                dpg.add_checkbox(
                    label="Enabled",
                    tag=checkbox_tag,
                    default_value=is_loaded,
                    callback=make_toggle_callback(name, info),
                )
                with dpg.child_window(
                    tag=container_tag, height=0, show=is_loaded, border=False,
                ):
                    if is_loaded:
                        _rebuild_plugin_ui(plugin_manager, name, container_tag)


def _rebuild_plugin_ui(plugin_manager, name: str, container_tag: str) -> None:
    """Clear container and populate with plugin's build_ui output."""
    if not dpg.does_item_exist(container_tag):
        return
    dpg.delete_item(container_tag, children_only=True)

    plugin = plugin_manager.plugins.get(name)
    if plugin is None:
        return

    try:
        plugin.build_ui(container_tag)
    except Exception:
        err_lines = traceback.format_exc().strip().split("\n")
        err_msg = err_lines[-1] if err_lines else "Unknown error"
        dpg.add_text(
            f"Error building UI: {err_msg}",
            parent=container_tag,
            color=(255, 80, 80),
        )


def _clear_plugin_ui(container_tag: str) -> None:
    """Remove all children from the plugin UI container."""
    if dpg.does_item_exist(container_tag):
        dpg.delete_item(container_tag, children_only=True)
