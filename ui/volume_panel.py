"""Volume / mic sliders, mute buttons, device selectors."""
import dearpygui.dearpygui as dpg

_on_master_change = None
_on_mic_change = None
_on_master_mute = None
_on_mic_mute = None
_on_output_device_change = None
_on_input_device_change = None
_on_master_cap_change = None
_on_mic_cap_change = None

_output_device_ids: list[str] = []
_input_device_ids: list[str] = []
_mute_btn_muted = None
_mute_btn_unmuted = None


def _get_mute_btn_theme(muted: bool):
    global _mute_btn_muted, _mute_btn_unmuted
    if _mute_btn_muted is None:
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (220, 60, 60, 255))
        _mute_btn_muted = t
    if _mute_btn_unmuted is None:
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (40, 40, 52, 255))
        _mute_btn_unmuted = t
    return _mute_btn_muted if muted else _mute_btn_unmuted


def create_volume_panel(
    parent="mixer_content",
    master_callback=None, mic_callback=None,
    master_mute_callback=None, mic_mute_callback=None,
    output_device_callback=None, input_device_callback=None,
    master_cap_callback=None, mic_cap_callback=None,
):
    global _on_master_change, _on_mic_change
    global _on_master_mute, _on_mic_mute
    global _on_output_device_change, _on_input_device_change
    global _on_master_cap_change, _on_mic_cap_change

    _on_master_change = master_callback
    _on_mic_change = mic_callback
    _on_master_mute = master_mute_callback
    _on_mic_mute = mic_mute_callback
    _on_output_device_change = output_device_callback
    _on_input_device_change = input_device_callback
    _on_master_cap_change = master_cap_callback
    _on_mic_cap_change = mic_cap_callback

    with dpg.group(parent=parent):
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_text("Output", color=(85, 150, 240))
            dpg.add_combo(tag="output_device_combo", items=["(default)"],
                          default_value="(default)", width=320,
                          callback=_output_combo_changed)

        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_text("Vol  ", color=(85, 150, 240))
            dpg.add_slider_int(tag="master_vol_slider", default_value=50,
                               min_value=0, max_value=100, width=240,
                               format="%d%%", callback=_master_slider_changed)
            dpg.add_button(label="M", tag="master_mute_btn", width=28,
                           callback=_master_mute_toggle)

        dpg.add_spacer(height=2)
        with dpg.group(horizontal=True):
            dpg.add_text("Limit", color=(200, 175, 70))
            dpg.add_slider_int(tag="master_cap_slider", default_value=100,
                               min_value=1, max_value=100, width=240,
                               format="%d%%",
                               callback=_master_cap_slider_changed)

        dpg.add_spacer(height=10)
        with dpg.group(horizontal=True):
            dpg.add_text("Input ", color=(240, 125, 85))
            dpg.add_combo(tag="input_device_combo", items=["(default)"],
                          default_value="(default)", width=320,
                          callback=_input_combo_changed)

        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_text("Mic  ", color=(240, 125, 85))
            dpg.add_slider_int(tag="mic_vol_slider", default_value=50,
                               min_value=0, max_value=100, width=240,
                               format="%d%%", callback=_mic_slider_changed)
            dpg.add_button(label="M", tag="mic_mute_btn", width=28,
                           callback=_mic_mute_toggle)

        dpg.add_spacer(height=2)
        with dpg.group(horizontal=True):
            dpg.add_text("Limit", color=(210, 160, 50))
            dpg.add_slider_int(tag="mic_cap_slider", default_value=100,
                               min_value=1, max_value=100, width=240,
                               format="%d%%",
                               callback=_mic_cap_slider_changed)


def populate_output_devices(devices, selected_id=None):
    _output_device_ids.clear()
    names = ["(default)"]
    _output_device_ids.append("")
    selected_name = "(default)"
    for dev_id, name in devices:
        _output_device_ids.append(dev_id)
        names.append(name)
        if dev_id == selected_id:
            selected_name = name
    dpg.configure_item("output_device_combo", items=names)
    dpg.set_value("output_device_combo", selected_name)


def populate_input_devices(devices, selected_id=None):
    _input_device_ids.clear()
    names = ["(default)"]
    _input_device_ids.append("")
    selected_name = "(default)"
    for dev_id, name in devices:
        _input_device_ids.append(dev_id)
        names.append(name)
        if dev_id == selected_id:
            selected_name = name
    dpg.configure_item("input_device_combo", items=names)
    dpg.set_value("input_device_combo", selected_name)


def _output_combo_changed(sender, value):
    if _on_output_device_change:
        items = dpg.get_item_configuration("output_device_combo")["items"]
        idx = items.index(value) if value in items else 0
        dev_id = _output_device_ids[idx] if idx < len(_output_device_ids) else ""
        _on_output_device_change(dev_id or None)

def _input_combo_changed(sender, value):
    if _on_input_device_change:
        items = dpg.get_item_configuration("input_device_combo")["items"]
        idx = items.index(value) if value in items else 0
        dev_id = _input_device_ids[idx] if idx < len(_input_device_ids) else ""
        _on_input_device_change(dev_id or None)

def _master_cap_slider_changed(s, v): _on_master_cap_change and _on_master_cap_change(v / 100.0)
def _mic_cap_slider_changed(s, v): _on_mic_cap_change and _on_mic_cap_change(v / 100.0)
def _master_slider_changed(s, v): _on_master_change and _on_master_change(v / 100.0)
def _mic_slider_changed(s, v): _on_mic_change and _on_mic_change(v / 100.0)
def _master_mute_toggle(): _on_master_mute and _on_master_mute()
def _mic_mute_toggle(): _on_mic_mute and _on_mic_mute()

def set_master_volume_display(level): dpg.set_value("master_vol_slider", int(level * 100))
def set_mic_volume_display(level): dpg.set_value("mic_vol_slider", int(level * 100))
def set_master_cap_display(cap): dpg.set_value("master_cap_slider", max(1, int(cap * 100)))
def set_mic_cap_display(cap): dpg.set_value("mic_cap_slider", max(1, int(cap * 100)))

def set_master_mute_display(muted):
    dpg.bind_item_theme("master_mute_btn", _get_mute_btn_theme(muted))

def set_mic_mute_display(muted):
    dpg.bind_item_theme("mic_mute_btn", _get_mute_btn_theme(muted))
