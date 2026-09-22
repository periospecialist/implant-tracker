"""Tkinter desktop UI. Forms are configured centrally; all network work is background work."""
import argparse
import csv
from datetime import date, timedelta
import json
import os
from pathlib import Path
import queue
import re
import sys
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk
from uuid import uuid4

from .appearance import Appearance
from .client import LocalStore, SyncEngine, Transport
from .core import COMPONENT_FIELDS, DETAIL_FIELDS, ValidationError, day, integer, normalize_event, ref_id
from .migration import prepare
from .settings import data_directory, load_config, save_config
from .update import build_summary, download_release, latest_release, launch_installer


INVENTORY_COLUMNS = [("ref_id", "REF ID"), ("manufacturer", "Manufacturer"), ("implant_type", "Implant type"),
    ("size", "Size"), ("component_type", "Component"), ("estimated_quantity", "Quantity"),
    ("reorder_level", "Reorder at"), ("reorder", "Reorder?")]
LOG_COLUMNS = [("date", "Date"), ("ref_id", "REF ID"), ("quantity", "Quantity"), ("tooth", "Tooth"),
    ("placement", "Placed / O Box"), ("patient", "Patient"), ("periodontist", "Periodontist"),
    ("referring_dentist", "Referring dentist"), ("assistant", "Assistant"), ("signed_by", "Signed off by"),
    ("notes", "Notes"), ("extra", "Custom fields"), ("affects_stock", "Changes stock"),
    ("actor", "Recorded by"), ("received_at", "Received (UTC)"), ("reversal_of", "Reverses entry"), ("id", "Entry ID")]
def display(value):
    return json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else str(value if value is not None else "")


def component_name(component):
    """The short clinical name shown while a REF ID is being verified."""
    return " ".join(str(component.get(key) or "").strip()
                    for key in ("manufacturer", "implant_type", "size")).strip()


def matching_refs(components, query, limit=50):
    """Prefix results first, then other REF-ID substring matches."""
    query = str(query or "").strip().casefold()
    identities = sorted(components, key=str.casefold)
    if not query:
        return identities[:limit]
    prefix = [identity for identity in identities if identity.casefold().startswith(query)]
    contains = [identity for identity in identities
                if query in identity.casefold() and identity not in prefix]
    return (prefix + contains)[:limit]


def matching_names(values, query, limit=50):
    """Case-insensitive name lookup with REF-ID-style prefix prioritization."""
    query = str(query or "").strip().casefold()
    unique = {}
    for value in values:
        name = str(value).strip()
        if name:
            unique.setdefault(name.casefold(), name)
    names = sorted(unique.values(), key=str.casefold)
    if not query:
        return names[:limit]
    prefix = [name for name in names if name.casefold().startswith(query)]
    contains = [name for name in names if query in name.casefold() and name not in prefix]
    return (prefix + contains)[:limit]


def csv_column_values(path, column_index):
    """Read one selected CSV column and deduplicate names case-insensitively."""
    values, seen = [], set()
    with open(path, newline="", encoding="utf-8-sig") as stream:
        rows = csv.reader(stream)
        headers = next(rows, None)
        if headers is None or not 0 <= column_index < len(headers):
            raise ValueError("Choose a valid CSV column")
        for row in rows:
            if column_index >= len(row):
                continue
            name = " ".join(row[column_index].split())
            identity = name.casefold()
            if name and identity not in seen:
                seen.add(identity)
                values.append(name)
    return values


def inventory_cell_display(key, value, row):
    if key == "estimated_quantity" and isinstance(value, (int, float)) and value < 0:
        return f"🟨 ERROR: {value}"
    if key == "reorder":
        return "🟥 YES" if value == "Yes" else "🟩 No"
    return display(value)


def inventory_row_style(row):
    manufacturer = str(row.get("manufacturer") or "").casefold()
    return next((name for name in ("straumann", "nobel") if name in manufacturer), "")


def natural_label_key(value):
    """Sort labels in human order, including embedded numbers."""
    return tuple(int(part) if part.isdigit() else part.casefold()
                 for part in re.split(r"(\d+)", str(value)))


def selected_or_any(value, selections):
    return not selections or value in selections


def available_implant_types(rows, manufacturers, component_types):
    return sorted({str(row.get("implant_type") or "Unspecified").strip() for row in rows
                   if selected_or_any(str(row.get("manufacturer") or "Unspecified").strip(), manufacturers)
                   and selected_or_any(str(row.get("component_type") or "Unspecified").strip(), component_types)},
                  key=natural_label_key)


def inventory_filter_matches(row, manufacturers, component_types, implant_types):
    return (selected_or_any(str(row.get("manufacturer") or "Unspecified").strip(), manufacturers)
            and selected_or_any(str(row.get("component_type") or "Unspecified").strip(), component_types)
            and selected_or_any(str(row.get("implant_type") or "Unspecified").strip(), implant_types))


class DataTable(ttk.Frame):
    """One reusable table with numeric sorting, AND filters, and filtered CSV export."""
    def __init__(self, parent, columns, row_style=None, cell_formatter=None,
                 show_manual_filters=True, inventory_style=False, show_export=True,
                 editable_columns=None):
        super().__init__(parent, padding=8)
        self.columns, self.rows, self.visible, self.filters = columns, [], [], {}
        self.row_style, self.cell_formatter = row_style, cell_formatter or (lambda key, value, row: display(value))
        self.predicate, self.preset_label = None, ""
        self.editable_columns = editable_columns or {}
        self.sort_key, self.reverse = columns[0][0], False
        toolbar = ttk.Frame(self)
        if show_manual_filters or show_export:
            toolbar.pack(fill="x", pady=(0, 6))
        self.column = ttk.Combobox(toolbar, values=[label for _, label in columns], state="readonly", width=22)
        self.column.current(0)
        if show_manual_filters:
            self.column.pack(side="left")
        self.query = ttk.Entry(toolbar, width=24)
        if show_manual_filters:
            self.query.pack(side="left", padx=5)
        self.query.bind("<Return>", lambda _: self.add_filter())
        if show_manual_filters:
            ttk.Button(toolbar, text="Apply filter", command=self.add_filter).pack(side="left")
            ttk.Button(toolbar, text="Clear filters", command=self.clear_filters).pack(side="left", padx=5)
        if show_export:
            ttk.Button(toolbar, text="Export shown rows", command=self.export).pack(side="right")
        self.caption = ttk.Label(self, text="", style="Muted.TLabel")
        if show_manual_filters:
            self.caption.pack(anchor="w", pady=(0, 6))
        holder = ttk.Frame(self)
        holder.pack(fill="both", expand=True)
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(holder, columns=[key for key, _ in columns], show="headings", selectmode="browse")
        for key, label in columns:
            self.tree.heading(key, text=label + " ↕", command=lambda k=key: self.sort(k))
            self.tree.column(key, width=145 if key in ("ref_id", "implant_type", "component_type") else 125, minwidth=65)
        self.reorder_tree = None
        if inventory_style:
            self.tree.configure(style="Inventory.Treeview",
                                displaycolumns=[key for key, _ in columns if key != "reorder"])
            self.reorder_tree = ttk.Treeview(holder, columns=("reorder",), show="headings",
                                            selectmode="browse", style="Reorder.Treeview")
            self.reorder_tree.heading("reorder", text="Reorder? ↕", command=lambda: self.sort("reorder"))
            self.reorder_tree.column("reorder", width=115, minwidth=115, stretch=False, anchor="center")
            self.reorder_tree.grid(row=0, column=1, sticky="nsew")
            self.reorder_tree.tag_configure("yes", background="#b42318", foreground="#ffffff")
            self.reorder_tree.tag_configure("no", background="#16703c", foreground="#ffffff")
            self._scroll_syncing = self._selection_syncing = False
            for tree in (self.tree, self.reorder_tree):
                tree.bind("<<TreeviewSelect>>", lambda event: self._sync_selection(event.widget))
        self.tree.grid(row=0, column=0, sticky="nsew")
        if self.editable_columns:
            self.tree.bind("<Double-Button-1>", self._begin_edit)
        vertical = ttk.Scrollbar(holder, orient="vertical", command=self._yview)
        vertical.grid(row=0, column=2 if inventory_style else 1, sticky="ns")
        horizontal = ttk.Scrollbar(holder, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        if self.reorder_tree is not None:
            for tree in (self.tree, self.reorder_tree):
                tree.configure(yscrollcommand=lambda first, last, source=tree:
                               self._sync_scroll(source, vertical, first, last))
        self.tree.tag_configure("negative", background="#ffe2e2")
        self.tree.tag_configure("low", background="#fff2cf")
        self.tree.tag_configure("straumann", background="#e5f2ff", foreground="#163c61")
        self.tree.tag_configure("nobel", background="#f0e9ff", foreground="#463268")

    def _begin_edit(self, event):
        """Overlay an editor on an explicitly editable Treeview cell."""
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        item = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        displayed = [key for key, _ in self.columns if key != "reorder" or self.reorder_tree is None]
        try:
            key = displayed[int(column_id[1:]) - 1]
            row = self.visible[int(item)]
        except (ValueError, IndexError):
            return
        callback = self.editable_columns.get(key)
        if callback is None:
            return
        bounds = self.tree.bbox(item, column_id)
        if not bounds:
            return
        editor = ttk.Entry(self.tree)
        editor.insert(0, display(row.get(key)))
        editor.select_range(0, "end")
        editor.place(x=bounds[0], y=bounds[1], width=bounds[2], height=bounds[3])
        editor.focus_set()
        finished = False
        def finish(save):
            nonlocal finished
            if finished:
                return
            finished = True
            value = editor.get().strip()
            editor.destroy()
            if save and value != display(row.get(key)):
                callback(row, value)
        editor.bind("<Return>", lambda _event: finish(True))
        editor.bind("<Escape>", lambda _event: finish(False))
        editor.bind("<FocusOut>", lambda _event: finish(True))

    def _yview(self, *args):
        self.tree.yview(*args)
        if self.reorder_tree is not None:
            self.reorder_tree.yview_moveto(self.tree.yview()[0])

    def _sync_scroll(self, source, scrollbar, first, last):
        scrollbar.set(first, last)
        if self._scroll_syncing:
            return
        other = self.reorder_tree if source is self.tree else self.tree
        if abs(other.yview()[0] - float(first)) < 0.000001:
            return
        self._scroll_syncing = True
        try:
            other.yview_moveto(first)
        finally:
            self._scroll_syncing = False

    def _sync_selection(self, source):
        if self._selection_syncing:
            return
        other = self.reorder_tree if source is self.tree else self.tree
        selection = source.selection()
        if selection == other.selection():
            return
        self._selection_syncing = True
        try:
            other.selection_set(selection)
            if selection:
                other.focus(selection[0])
        finally:
            self._selection_syncing = False

    def add_filter(self):
        key = self.columns[self.column.current()][0]
        value = self.query.get().strip()
        if value:
            self.filters[key] = value
        else:
            self.filters.pop(key, None)
        self.render()

    def clear_filters(self):
        self.filters.clear()
        self.query.delete(0, "end")
        self.render()

    def set_preset(self, label, predicate):
        self.preset_label, self.predicate = label, predicate
        self.render()

    def sort(self, key):
        self.reverse = not self.reverse if key == self.sort_key else False
        self.sort_key = key
        self.render()

    def set_rows(self, rows):
        self.rows = rows
        self.render()

    def render(self):
        selected = self.selected()
        self.visible = [row for row in self.rows
                        if (self.predicate is None or self.predicate(row))
                        and all(value.casefold() in display(row.get(key)).casefold()
                                for key, value in self.filters.items())]
        def sort_value(row):
            value = row.get(self.sort_key, "")
            if isinstance(value, (int, float)):
                return (0, value)
            return (1, display(value).casefold())
        self.visible.sort(key=sort_value, reverse=self.reverse)
        self.tree.delete(*self.tree.get_children())
        if self.reorder_tree is not None:
            self.reorder_tree.delete(*self.reorder_tree.get_children())
        restore = None
        for index, row in enumerate(self.visible):
            tag = self.row_style(row) if self.row_style else ""
            self.tree.insert("", "end", iid=str(index),
                             values=[self.cell_formatter(key, row.get(key), row) for key, _ in self.columns], tags=(tag,))
            if self.reorder_tree is not None:
                needs_reorder = row.get("reorder") == "Yes"
                self.reorder_tree.insert("", "end", iid=str(index),
                                         values=("YES" if needs_reorder else "NO",),
                                         tags=("yes" if needs_reorder else "no",))
            if selected and row.get("id", row.get("ref_id")) == selected.get("id", selected.get("ref_id")):
                restore = str(index)
        if restore is not None:
            self.tree.selection_set(restore)
        parts = (["Preset: " + self.preset_label] if self.preset_label else [])
        parts += [f"{dict(self.columns)[k]} contains {v}" for k, v in self.filters.items()]
        active = "; ".join(parts)
        self.caption.configure(text=f"{len(self.visible)} of {len(self.rows)} rows" + (f" · {active}" if active else " · Click a column heading to sort"))

    def selected(self):
        selected = self.tree.selection()
        return self.visible[int(selected[0])] if selected and int(selected[0]) < len(self.visible) else None

    def export(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow([label for _, label in self.columns])
                for row in self.visible:
                    values = []
                    for key, _ in self.columns:
                        value = display(row.get(key))
                        if value.lstrip().startswith(("=", "+", "-", "@")) and not isinstance(row.get(key), (int, float)):
                            value = "'" + value  # Prevent spreadsheet formula execution from text fields.
                        values.append(value)
                    writer.writerow(values)
        except OSError as error:
            messagebox.showerror("Export failed", str(error), parent=self)


class PersistentCheckDropdown(ttk.Frame):
    """Checkbox dropdown that remains open while choices are toggled."""
    def __init__(self, parent, width=24):
        super().__init__(parent)
        self.button = ttk.Button(self, text="Any ▼", width=width, command=self.toggle)
        self.button.pack(fill="x")
        self.popup = tk.Toplevel(self)
        self.popup.withdraw()
        self.popup.overrideredirect(True)
        self.popup.transient(self.winfo_toplevel())
        self.panel = ttk.Frame(self.popup, padding=6, relief="solid", borderwidth=1)
        self.panel.pack(fill="both", expand=True)
        self.visible = False
        self.winfo_toplevel().bind_all("<Button-1>", self._outside_click, add="+")
        self.popup.bind("<Escape>", lambda _event: self.hide())

    @staticmethod
    def _descends_from(widget, ancestor):
        while widget is not None:
            if widget == ancestor:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _outside_click(self, event):
        if (self.visible and not self._descends_from(event.widget, self)
                and not self._descends_from(event.widget, self.popup)):
            self.hide()

    def rebuild(self, values, variables, clear_command, change_command):
        for widget in self.panel.winfo_children():
            widget.destroy()
        ttk.Button(self.panel, text="Any (clear selection)", command=clear_command).pack(fill="x", anchor="w")
        ttk.Separator(self.panel).pack(fill="x", pady=4)
        for value in values:
            ttk.Checkbutton(self.panel, text=value, variable=variables[value],
                            command=change_command).pack(fill="x", anchor="w", pady=1)
        if self.visible:
            self.after_idle(self._position)

    def set_text(self, text):
        self.button.configure(text=text + " ▼")

    def _position(self):
        self.popup.update_idletasks()
        x = self.button.winfo_rootx()
        y = self.button.winfo_rooty() + self.button.winfo_height()
        width = max(self.button.winfo_width(), self.popup.winfo_reqwidth())
        height = self.popup.winfo_reqheight()
        screen_height = self.winfo_screenheight()
        if y + height > screen_height:
            y = max(0, self.button.winfo_rooty() - height)
        self.popup.geometry(f"{width}x{height}+{x}+{y}")

    def show(self):
        self.visible = True
        self._position()
        self.popup.deiconify()
        self.popup.lift()

    def hide(self):
        self.visible = False
        self.popup.withdraw()

    def toggle(self):
        self.hide() if self.visible else self.show()


class InventoryFilters(ttk.Frame):
    """Inclusive manufacturer/component toggles and an implant-type menu."""
    GROUPS = (("manufacturer", "Manufacturer"), ("component_type", "Component Type"),
              ("implant_type", "Implant Type"))

    def __init__(self, parent, table):
        super().__init__(parent, padding=(8, 4))
        self.table, self.rows = table, []
        self.variables = {key: {} for key, _ in self.GROUPS}
        self.signatures, self.buttons, self.groups, self.labels = {}, {}, {}, {}
        layout = {
            "manufacturer": (0, 0, 1),
            "component_type": (0, 2, 3),
            "implant_type": (1, 0, 1),
        }
        for column, title in self.GROUPS:
            row, label_column, group_column = layout[column]
            label = ttk.Label(self, text=title + ":")
            label.grid(row=row, column=label_column, sticky="w", padx=(2, 8), pady=3)
            self.labels[column] = label
            group = ttk.Frame(self)
            group.grid(row=row, column=group_column, sticky="w", padx=(0, 16), pady=3)
            self.groups[column] = group
            if column == "implant_type":
                button = PersistentCheckDropdown(group, width=24)
                button.pack(anchor="w")
                self.buttons[column] = button
        self.export_button = ttk.Button(self, text="Export shown rows", command=self.table.export)
        self.export_button.grid(row=1, column=3, sticky="e", padx=(16, 2), pady=3)
        self.columnconfigure(3, weight=1)

    @staticmethod
    def values(rows, column):
        return sorted({str(row.get(column) or "Unspecified").strip() for row in rows}, key=natural_label_key)

    def update_rows(self, rows):
        self.rows = rows
        self.table.set_rows(rows)
        self._render("manufacturer", self.values(rows, "manufacturer"), self._parents_changed)
        self._render("component_type", self.values(rows, "component_type"), self._parents_changed)
        self._refresh_implant_types()
        self.apply()

    def _selected(self, column):
        return {value for value, variable in self.variables[column].items() if variable.get()}

    def _parent_match(self, row):
        manufacturer = str(row.get("manufacturer") or "Unspecified").strip()
        component = str(row.get("component_type") or "Unspecified").strip()
        return (selected_or_any(manufacturer, self._selected("manufacturer"))
                and selected_or_any(component, self._selected("component_type")))

    def _refresh_implant_types(self):
        available = available_implant_types(self.rows, self._selected("manufacturer"),
                                            self._selected("component_type"))
        self._render("implant_type", available, self.apply)

    def _parents_changed(self):
        self._refresh_implant_types()
        self.apply()

    def _render(self, column, values, command):
        signature = tuple(values)
        if self.signatures.get(column) == signature:
            self._update_button(column)
            return
        existing = self.variables[column]
        self.variables[column] = {value: existing[value] if value in existing else tk.BooleanVar(self, value=False)
                                  for value in values}
        if column != "implant_type":
            group = self.groups[column]
            for widget in group.winfo_children():
                widget.destroy()
            any_button = ttk.Button(group, text="Any", command=lambda c=column, f=command: self.select(c, False, f))
            any_button.grid(row=0, column=0, sticky="w", padx=(0, 5), pady=2)
            self.buttons[column] = any_button
            for index, value in enumerate(values):
                button = ttk.Checkbutton(group, text=value, variable=self.variables[column][value],
                                         style="Filter.Toolbutton",
                                         command=lambda c=column, f=command: self._choice_changed(c, f))
                button.grid(row=0, column=1 + index, sticky="w", padx=(0, 5), pady=2)
            self.signatures[column] = signature
            self._update_button(column)
            return
        self.buttons[column].rebuild(
            values,
            self.variables[column],
            lambda c=column, f=command: self.select(c, False, f),
            lambda c=column, f=command: self._choice_changed(c, f),
        )
        self.signatures[column] = signature
        self._update_button(column)

    def _choice_changed(self, column, command):
        self._update_button(column)
        command()

    def _update_button(self, column):
        selected = sorted(self._selected(column), key=natural_label_key)
        if not selected:
            label = "Any"
        elif len(selected) == 1:
            label = selected[0]
        else:
            label = f"{len(selected)} selected"
        if column == "implant_type":
            self.buttons[column].set_text(label)
        elif column in self.buttons:
            self.buttons[column].configure(text="Any", style="ActiveFilter.TButton" if not selected else "TButton")

    def select(self, column, selected, command=None):
        for variable in self.variables[column].values():
            variable.set(selected)
        self._update_button(column)
        (command or self.apply)()

    def clear_selections(self):
        for column in ("manufacturer", "component_type", "implant_type"):
            for variable in self.variables[column].values():
                variable.set(False)
            self._update_button(column)
        self._refresh_implant_types()
        self.apply()

    def matches(self, row):
        return inventory_filter_matches(row, self._selected("manufacturer"),
                                        self._selected("component_type"),
                                        self._selected("implant_type"))

    def apply(self):
        self.table.predicate = self.matches
        self.table.preset_label = "checkbox selections"
        self.table.render()


class EntryForm(ttk.Frame):
    def __init__(self, parent, app, kind, fields):
        super().__init__(parent, padding=10)
        self.app, self.kind, self.fields = app, kind, fields
        self.variables, self.widgets = {}, {}
        self.choice_fields = {field["key"]: list(field.get("choices", [])) for field in fields
                              if field["key"] in ("periodontist", "referring_dentist", "assistant")}
        self.refs = []
        title = "Record component usage" if kind == "usage" else "Add components to stock"
        ttk.Label(self, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(self, text="Saved locally. Synced automatically.   * Required field", style="Muted.TLabel").pack(anchor="w", pady=(5, 12))
        canvas = tk.Canvas(self, highlightthickness=0, background="#f5f7fa")
        scroll = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        form = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        base = [{"key": "date", "label": "Date (YYYY-MM-DD)", "required": True},
                {"key": "ref_id", "label": "REF ID", "required": True, "choices": []},
                {"key": "quantity", "label": "Quantity used" if kind == "usage" else "Quantity added", "required": True}]
        widths = {"date": 16, "ref_id": 27, "quantity": 10, "tooth": 10, "placement": 17,
                  "patient": 32, "periodontist": 24, "referring_dentist": 32,
                  "assistant": 24, "signed_by": 24, "notes": 42}
        for index, field in enumerate(base + fields):
            key = field["key"]
            variable = tk.StringVar(value=date.today().isoformat() if key == "date" else "1" if key == "quantity" else "")
            self.variables[key] = variable
            ttk.Label(form, text=field["label"] + (" *" if field.get("required") else "")).grid(row=index, column=0, sticky="w", padx=(0, 18), pady=5)
            if key == "placement":
                widget = ttk.Combobox(form, textvariable=variable, values=("Placed", "O Box"),
                                      state="readonly", width=widths[key])
            elif "choices" in field:
                widget = ttk.Combobox(form, textvariable=variable, values=field["choices"],
                                      width=widths.get(key, 30))
            else:
                widget = ttk.Entry(form, textvariable=variable, width=widths.get(key, 30))
            self.widgets[key] = widget
            widget.grid(row=index, column=1, sticky="w", pady=5)
            if key == "ref_id":
                self.ref_widget = widget
                widget.bind("<KeyRelease>", self.search_refs)
                widget.bind("<Return>", self.accept_ref_match)
                widget.bind("<<ComboboxSelected>>", self.ref_selected)
                widget.bind("<FocusOut>", self.normalize_exact_ref)
                ttk.Button(form, text="Search / select", command=self.open_ref_search).grid(row=index, column=2, padx=(8, 0), pady=5)
            elif key in self.choice_fields:
                widget.bind("<KeyRelease>", lambda event, k=key: self.search_name(k, event))
                widget.bind("<Return>", lambda event, k=key: self.accept_name_match(k))
                widget.bind("<<ComboboxSelected>>", lambda _event, k=key: self.normalize_exact_name(k))
                widget.bind("<FocusOut>", lambda _event, k=key: self.normalize_exact_name(k))
                ttk.Button(form, text="Search / select",
                           command=lambda k=key: self.open_name_search(k)).grid(
                               row=index, column=2, padx=(8, 0), pady=5)
        self.description = ttk.Label(form, text="Start typing a REF ID to search inventory.",
                                     wraplength=760, padding=(12, 10), style="Lookup.TLabel")
        self.description.grid(row=len(base + fields), column=0, columnspan=3, sticky="ew", pady=15)
        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(15, 0))
        ttk.Button(actions, text="Save usage" if kind == "usage" else "Save stocking entry", style="Accent.TButton", command=self.save).pack(side="left")
        ttk.Button(actions, text="Clear entry", command=self.clear).pack(side="left", padx=8)
        self.notice = ttk.Label(actions, text="")
        self.notice.pack(side="left", padx=8)

    def search_refs(self, event=None):
        if event is not None and event.keysym in ("Up", "Down", "Left", "Right", "Return", "Tab", "Escape"):
            return
        query = self.variables["ref_id"].get().strip()
        self.filtered_refs = matching_refs(getattr(self, "components", {}), query)
        self.ref_widget["values"] = self.filtered_refs
        self.describe_ref()

    def search_name(self, key, event=None):
        if event is not None and event.keysym in ("Up", "Down", "Left", "Right", "Return", "Tab", "Escape"):
            return
        self.widgets[key]["values"] = matching_names(self.choice_fields[key], self.variables[key].get())

    def accept_name_match(self, key):
        query = self.variables[key].get().strip()
        exact = next((name for name in self.choice_fields[key] if name.casefold() == query.casefold()), None)
        matches = matching_names(self.choice_fields[key], query)
        if exact:
            self.variables[key].set(exact)
        elif matches:
            self.variables[key].set(matches[0])
        else:
            self.bell()
        self.search_name(key)
        self.widgets[key].icursor("end")
        return "break"

    def normalize_exact_name(self, key):
        query = self.variables[key].get().strip()
        exact = next((name for name in self.choice_fields[key] if name.casefold() == query.casefold()), None)
        if exact:
            self.variables[key].set(exact)
        self.search_name(key)

    def open_name_search(self, key):
        field = next(field for field in self.fields if field["key"] == key)
        window = tk.Toplevel(self)
        window.title("Search " + field["label"])
        window.transient(self.winfo_toplevel())
        window.geometry("620x420")
        container = ttk.Frame(window, padding=14)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="Type any part of the name", style="Title.TLabel").pack(anchor="w")
        query = tk.StringVar(value=self.variables[key].get().strip())
        search = ttk.Entry(container, textvariable=query, width=42)
        search.pack(fill="x", pady=(8, 10))
        results = tk.Listbox(container, font=("Segoe UI", 12), activestyle="dotbox", exportselection=False)
        results.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=results.yview)
        scrollbar.pack(side="right", fill="y")
        results.configure(yscrollcommand=scrollbar.set)
        names = []
        def refresh(*_):
            nonlocal names
            names = matching_names(self.choice_fields[key], query.get(), limit=500)
            results.delete(0, "end")
            for name in names:
                results.insert("end", name)
            if names:
                results.selection_set(0)
                results.activate(0)
        def choose(event=None):
            selected = results.curselection()
            if not selected:
                self.bell()
                return "break"
            self.variables[key].set(names[selected[0]])
            self.search_name(key)
            window.destroy()
            self.widgets[key].focus_set()
            return "break"
        query.trace_add("write", refresh)
        search.bind("<Return>", choose)
        results.bind("<Return>", choose)
        results.bind("<Double-Button-1>", choose)
        refresh()
        search.focus_set()

    def open_ref_search(self):
        self.search_refs()
        window = tk.Toplevel(self)
        window.title("Search inventory REF IDs")
        window.transient(self.winfo_toplevel())
        window.geometry("760x440")
        container = ttk.Frame(window, padding=14)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="Type any part of the REF ID", style="Title.TLabel").pack(anchor="w")
        query = tk.StringVar(value=self.variables["ref_id"].get().strip())
        search = ttk.Entry(container, textvariable=query, width=42)
        search.pack(fill="x", pady=(8, 10))
        results = tk.Listbox(container, font=("Segoe UI", 12), activestyle="dotbox", exportselection=False)
        results.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=results.yview)
        scrollbar.pack(side="right", fill="y")
        results.configure(yscrollcommand=scrollbar.set)
        identities = []
        def refresh(*_):
            nonlocal identities
            identities = matching_refs(getattr(self, "components", {}), query.get(), limit=250)
            results.delete(0, "end")
            for identity in identities:
                results.insert("end", f"{identity}   —   {component_name(self.components[identity])}")
            if identities:
                results.selection_set(0)
                results.activate(0)
        def choose(event=None):
            selected = results.curselection()
            if not selected:
                self.bell()
                return "break"
            self.variables["ref_id"].set(identities[selected[0]])
            self.search_refs()
            self.ref_widget.icursor("end")
            window.destroy()
            self.ref_widget.focus_set()
            return "break"
        query.trace_add("write", refresh)
        search.bind("<Return>", choose)
        results.bind("<Return>", choose)
        results.bind("<Double-Button-1>", choose)
        refresh()
        search.focus_set()

    def accept_ref_match(self, event=None):
        query = self.variables["ref_id"].get().strip().upper()
        if query in getattr(self, "components", {}):
            selected = query
        else:
            matches = matching_refs(getattr(self, "components", {}), query)
            if not matches:
                self.bell()
                return "break"
            selected = matches[0]
        self.variables["ref_id"].set(selected)
        self.search_refs()
        self.ref_widget.icursor("end")
        return "break"

    def ref_selected(self, event=None):
        self.normalize_exact_ref()

    def normalize_exact_ref(self, event=None):
        query = self.variables["ref_id"].get().strip().upper()
        if query in getattr(self, "components", {}):
            self.variables["ref_id"].set(query)
        self.search_refs()

    def update_refs(self, rows):
        self.components = {row["ref_id"]: row for row in rows}
        self.refs = list(self.components)
        self.search_refs()
        self.describe_ref()

    def describe_ref(self):
        query = self.variables["ref_id"].get().strip()
        row = getattr(self, "components", {}).get(query.upper())
        if row:
            self.description.configure(
                text=(f"✓ REF ID verified\nItem: {component_name(row)}\n"
                      f"Component: {row['component_type']}  •  Estimated on hand: {row['estimated_quantity']}"),
                style="Verified.Lookup.TLabel")
            return
        matches = matching_refs(getattr(self, "components", {}), query)
        if not query:
            message = "Start typing a REF ID, choose Search, or use the dropdown arrow."
        elif matches:
            best = self.components[matches[0]]
            message = (f"{len(matches)} matching REF ID{'s' if len(matches) != 1 else ''}. "
                       f"Best match: {matches[0]} — {component_name(best)}\n"
                       "Press Enter to autofill the best match, or open the list to choose another.")
        else:
            message = "No inventory REF ID matches this search. Check the ID or sync the component list."
        self.description.configure(text=message, style="Lookup.TLabel")

    def save(self):
        try:
            values = {key: variable.get().strip() for key, variable in self.variables.items()}
            for field in self.fields:
                if field.get("required") and not values.get(field["key"]):
                    raise ValidationError(field["label"] + " is required")
            identity = ref_id(values["ref_id"])
            if identity not in getattr(self, "components", {}):
                raise ValidationError("Choose a REF ID from the component list. Sync first if this is a new computer.")
            entry_date = day(values["date"])
            if entry_date > date.today().isoformat() or entry_date <= self.components[identity]["opening_date"]:
                raise ValidationError("Date must be after the opening balance date and no later than today")
            quantity = integer(values["quantity"], 1)
            if self.kind == "usage" and quantity > self.components[identity]["estimated_quantity"]:
                if not messagebox.askyesno("Stock count needs review", "This usage exceeds the estimated stock. Save the actual usage and flag the balance for review?", parent=self):
                    return
            data = {key: value for key, value in values.items() if key in DETAIL_FIELDS or key in ("date", "ref_id", "quantity")}
            data.update(kind=self.kind, extra={k: v for k, v in values.items() if k not in data})
            self.app.store.queue(data)
        except Exception as error:
            messagebox.showerror("Entry could not be saved", str(error), parent=self)
            return
        try:
            self.clear()
            self.notice.configure(text="Saved on this computer. Waiting to sync.")
            self.app.refresh()
            self.app.start_sync()
        except Exception as error:
            messagebox.showerror("Entry saved; display needs refresh", "Your entry was saved. Do not enter it again.\n" + str(error), parent=self)

    def clear(self):
        for key, variable in self.variables.items():
            if key not in ("assistant", "signed_by", "periodontist"):
                variable.set(date.today().isoformat() if key == "date" else "1" if key == "quantity" else "")
        self.notice.configure(text="")
        self.describe_ref()


class App(tk.Tk):
    def __init__(self, config_path=None):
        super().__init__()
        self.title("Implant Inventory")
        self.geometry("1420x900")
        self.minsize(1080, 720)
        self.config_path = Path(config_path or data_directory() / "client.json")
        self.config = load_config(self.config_path)
        self.store = LocalStore(self.config_path.parent / "offline-cache.sqlite3")
        self.engine = SyncEngine(self.store, Transport(self.config))
        self.results = queue.Queue()
        self.syncing, self.online, self.closing = False, False, False
        self.appearance = Appearance(self, self.config.get("appearance", "Light"))
        header = ttk.Frame(self, padding=(10, 6), style="Chrome.TFrame")
        header.pack(fill="x")
        self.status = ttk.Label(header, text="Connecting…", style="Chrome.TLabel")
        self.status.pack(side="left")
        ttk.Button(header, text="Sync now", command=self.start_sync).pack(side="right", padx=(12, 0))
        self.appearance_choice = tk.StringVar(value=self.appearance.mode)
        selector = ttk.Combobox(header, textvariable=self.appearance_choice,
                                values=("Light", "Dark"), state="readonly", width=7)
        selector.pack(side="right")
        selector.bind("<<ComboboxSelected>>", self.change_appearance)
        ttk.Label(header, text="Appearance", style="Chrome.TLabel").pack(side="right", padx=10)
        self.summary = ttk.Label(self, padding=(10, 4), text="", style="Chrome.TLabel")
        self.summary.pack(side="bottom", fill="x")
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        self.forms = {}
        cached = self.store.snapshot()
        self.fields = cached.get("fields", self.config["fields"])
        for kind, title in (("usage", "Usage"), ("addition", "Stocking")):
            form = EntryForm(self.notebook, self, kind, self.fields[kind])
            self.forms[kind] = form
            self.notebook.add(form, text=title)
        inventory_frame = ttk.Frame(self.notebook)
        self.notebook.add(inventory_frame, text="Inventory")
        ttk.Label(inventory_frame, text="Inventory", style="Title.TLabel", padding=(10, 6)).pack(anchor="w")
        controls = ttk.Frame(inventory_frame, padding=(8, 8, 8, 0))
        controls.pack(fill="x")
        ttk.Button(controls, text="All stock", command=self.all_stock).pack(side="left")
        ttk.Button(controls, text="Needs reorder", command=self.low_stock).pack(side="left", padx=5)
        ttk.Button(controls, text="Add component", command=lambda: self.component_editor()).pack(side="right")
        ttk.Button(controls, text="Edit selected component", command=lambda: self.component_editor(self.inventory.selected())).pack(side="right", padx=5)
        self.inventory = DataTable(inventory_frame, INVENTORY_COLUMNS, row_style=inventory_row_style,
                                   cell_formatter=inventory_cell_display, show_manual_filters=False,
                                   inventory_style=True, show_export=False,
                                   editable_columns={"reorder_level": self.update_reorder_level})
        self.inventory_filters = InventoryFilters(inventory_frame, self.inventory)
        self.inventory_filters.pack(fill="x", padx=4, pady=(4, 0))
        self.inventory.pack(fill="both", expand=True)
        self.log_tables = {}
        for kind, title in (("usage", "Usage history"), ("addition", "Stock history")):
            frame = ttk.Frame(self.notebook)
            self.notebook.add(frame, text=title)
            top = ttk.Frame(frame, padding=(8, 8, 8, 0))
            top.pack(fill="x")
            ttk.Label(top, text="Latest 2,000 entries per log. Older entries remain in the server database.").pack(side="left")
            if kind == "usage":
                ttk.Button(top, text="Remove selected entry", command=self.remove_usage_entry).pack(side="right")
            table = DataTable(frame, LOG_COLUMNS)
            table.pack(fill="both", expand=True)
            self.log_tables[kind] = table
        pending_frame = ttk.Frame(self.notebook)
        self.notebook.add(pending_frame, text="Sync queue")
        top = ttk.Frame(pending_frame, padding=(8, 8, 8, 0))
        top.pack(fill="x")
        ttk.Label(top, text="Rejected entries stay here until corrected. Uncertain deliveries are retried safely.").pack(side="left")
        ttk.Button(top, text="Retry selected", command=self.retry_selected).pack(side="right")
        ttk.Button(top, text="Cancel rejected entry", command=self.cancel_selected).pack(side="right", padx=5)
        self.pending = DataTable(pending_frame, [("state", "Status"), ("error", "Issue"), ("kind", "Entry type")] + LOG_COLUMNS)
        self.pending.pack(fill="both", expand=True)
        self.setup_tab()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_after = self.after(100, self.poll)
        self.initial_sync_after = self.after(300, self.start_sync)
        self.periodic_after = self.after(self.config["sync_seconds"] * 1000, self.periodic)
        self.update_after = None
        if os.name == "nt" and getattr(sys, "frozen", False):
            self.update_after = self.after(2500, lambda: self.check_for_updates(silent=True))
        self.refresh()

    def change_appearance(self, event=None):
        self.appearance.apply(self.appearance_choice.get())
        self.config["appearance"] = self.appearance.mode
        try:
            save_config(self.config_path, self.config)
        except OSError as error:
            messagebox.showerror("Appearance could not be saved", str(error), parent=self)

    def background(self, function, success, failure=None):
        def run():
            try:
                self.results.put((success, function()))
            except Exception as error:
                self.results.put((failure or (lambda value: messagebox.showerror("Action failed", str(value), parent=self)), error))
        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        try:
            while True:
                callback, result = self.results.get_nowait()
                try:
                    callback(result)
                except tk.TclError:
                    pass  # A dialog can be closed while its background request finishes.
        except queue.Empty:
            pass
        if not self.closing:
            self.poll_after = self.after(100, self.poll)

    def periodic(self):
        self.start_sync()
        if not self.closing:
            self.periodic_after = self.after(self.config["sync_seconds"] * 1000, self.periodic)

    def start_sync(self):
        if self.syncing or self.closing:
            return
        self.syncing = True
        self.status.configure(text="Syncing… local saving remains available")
        def success(snapshot):
            self.syncing, self.online = False, True
            self.status.configure(text=f"Folder sync ready • Host: {snapshot.get('host_computer', 'inventory computer')}")
            self.connection_message.configure(text="Exchange folder connected. Entries are processed while the host is running.")
            if hasattr(self, "export_vars"):
                settings = snapshot.get("export_settings", {})
                for key, variable in self.export_vars.items():
                    if not variable.get():
                        variable.set(settings.get(key, ""))
            if snapshot.get("fields") != self.fields:
                self.fields = snapshot["fields"]
                for index, kind in enumerate(("usage", "addition")):
                    old = self.forms[kind]
                    values = {k: v.get() for k, v in old.variables.items()}
                    title = "Usage" if kind == "usage" else "Stocking"
                    self.notebook.forget(old)
                    form = EntryForm(self.notebook, self, kind, self.fields[kind])
                    self.notebook.insert(index, form, text=title)
                    self.forms[kind] = form
                    for key, value in values.items():
                        if key in form.variables:
                            form.variables[key].set(value)
                    old.destroy()
            self.refresh()
        def failure(error):
            self.syncing, self.online = False, False
            self.status.configure(text="Host / folder unavailable • entries saved locally")
            self.connection_message.configure(text=str(error))
            self.refresh()
        self.background(self.engine.sync, success, failure)

    def refresh(self):
        snapshot, entries, rows = self.store.view()
        self.inventory_filters.update_rows(rows)
        self.pending.set_rows(entries)
        for kind, table in self.log_tables.items():
            table.set_rows(snapshot["logs"][kind])
        for form in self.forms.values():
            form.update_refs(rows)
        errors = sum(e["state"] == "error" for e in entries)
        needs = sum(row["reorder"] == "Yes" for row in rows)
        self.summary.configure(text=f"{len(rows)} components    ·    {needs} need reorder    ·    "
            f"{len(entries)} queued ({errors} need attention)    ·    Quantities include this computer's pending entries")

    def all_stock(self):
        self.inventory.filters.clear()
        self.inventory.query.delete(0, "end")
        self.inventory_filters.clear_selections()
        self.inventory.clear_filters()

    def low_stock(self):
        self.inventory.filters["reorder"] = "Yes"
        self.inventory.render()

    def retry_selected(self):
        row = self.pending.selected()
        if row:
            self.store.retry(row["id"])
            self.refresh()
            self.start_sync()

    def cancel_selected(self):
        row = self.pending.selected()
        if not row:
            return
        if row["state"] != "error":
            return messagebox.showinfo("Delivery may be pending", "Only a server-rejected entry can be cancelled. Sync accepted entries before removing them from Usage history.", parent=self)
        if messagebox.askyesno("Cancel rejected entry", "Retain a local cancellation record and remove this rejected entry from the queue? Enter a corrected replacement afterward.", parent=self):
            self.store.cancel_rejected(row["id"])
            self.refresh()

    def remove_usage_entry(self):
        row = self.log_tables["usage"].selected()
        if not row:
            return
        if not messagebox.askyesno(
                "Remove usage entry",
                f"Remove this usage entry for {row['ref_id']} and restore {row['quantity']} to inventory?\n\n"
                "This permanently removes the history row.", parent=self):
            return
        self.status.configure(text="Removing usage entry…")
        def remove():
            result = self.engine.transport.post("/delete_usage", {"id": row["id"]})
            self.store.install_snapshot(result["snapshot"])
            return result
        def success(_):
            self.online = True
            self.status.configure(text="Usage entry removed • inventory quantity restored")
            self.refresh()
        def failure(error):
            self.status.configure(text="Could not remove usage entry")
            messagebox.showerror("Removal failed", str(error), parent=self)
        self.background(remove, success, failure)

    def setup_tab(self):
        setup_tab = ttk.Frame(self.notebook)
        self.notebook.add(setup_tab, text="Setup")
        canvas = tk.Canvas(setup_tab, highlightthickness=0)
        scrollbar = ttk.Scrollbar(setup_tab, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        setup = ttk.Frame(canvas, padding=22)
        content = canvas.create_window((0, 0), window=setup, anchor="nw")
        setup.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content, width=event.width))
        ttk.Label(setup, text="Shared-folder setup", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 15))
        self.connection_vars = {}
        for index, (key, label) in enumerate((("exchange_folder", "Inventory Exchange folder"),), 1):
            ttk.Label(setup, text=label).grid(row=index, column=0, sticky="w", pady=7, padx=(0, 20))
            variable = tk.StringVar(value=self.config.get(key, ""))
            self.connection_vars[key] = variable
            ttk.Entry(setup, textvariable=variable, width=66).grid(row=index, column=1, sticky="ew", pady=7)
        def browse_folder():
            folder = filedialog.askdirectory(parent=self, title="Select the shared Inventory Exchange folder")
            if folder:
                self.connection_vars["exchange_folder"].set(folder)
        ttk.Button(setup, text="Browse…", command=browse_folder).grid(row=1, column=2, padx=8)
        ttk.Button(setup, text="Save folder and sync", command=self.save_connection).grid(row=4, column=1, sticky="w", pady=12)
        self.connection_message = ttk.Label(setup, text="Select the same shared folder on every computer, such as \\\\SERVER\\Perio\\IMPLANTS\\Inventory Exchange. No login or access key is needed.", wraplength=850)
        self.connection_message.grid(row=5, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Separator(setup).grid(row=6, column=0, columnspan=3, sticky="ew", pady=20)
        ttk.Label(setup, text="Automatic inventory CSV exports", style="Title.TLabel").grid(row=7, column=0, columnspan=2, sticky="w")
        ttk.Label(setup, text="These settings are stored and executed only by the inventory host. The current file is replaced whenever inventory changes; the backup folder receives a separate dated CSV.", wraplength=850).grid(row=8, column=0, columnspan=3, sticky="w", pady=(6, 10))
        self.export_vars = {
            "inventory_csv_path": tk.StringVar(),
            "csv_backup_directory": tk.StringVar(),
        }
        ttk.Label(setup, text="Current inventory CSV file").grid(row=9, column=0, sticky="w", pady=7, padx=(0, 20))
        ttk.Entry(setup, textvariable=self.export_vars["inventory_csv_path"], width=66).grid(row=9, column=1, sticky="ew", pady=7)
        ttk.Button(setup, text="Browse…", command=self.browse_inventory_csv).grid(row=9, column=2, padx=8)
        ttk.Label(setup, text="Dated CSV backup folder").grid(row=10, column=0, sticky="w", pady=7, padx=(0, 20))
        ttk.Entry(setup, textvariable=self.export_vars["csv_backup_directory"], width=66).grid(row=10, column=1, sticky="ew", pady=7)
        ttk.Button(setup, text="Browse…", command=self.browse_csv_backup_folder).grid(row=10, column=2, padx=8)
        ttk.Button(setup, text="Save host export settings", command=self.save_export_settings).grid(row=11, column=1, sticky="w", pady=10)
        self.export_message = ttk.Label(setup, text="Leave either field blank to disable that export.", wraplength=850)
        self.export_message.grid(row=12, column=0, columnspan=3, sticky="w")
        ttk.Separator(setup).grid(row=13, column=0, columnspan=3, sticky="ew", pady=20)
        ttk.Label(setup, text="Clinical name directories", style="Title.TLabel").grid(row=14, column=0, columnspan=2, sticky="w")
        ttk.Label(setup, text="Add or remove Periodontists, Referring Dentists, and Assistants. Referring Dentists can also be imported from a selected CSV column.", wraplength=850).grid(row=15, column=0, columnspan=3, sticky="w", pady=(6, 10))
        ttk.Button(setup, text="Manage names…", command=self.staff_directory_dialog).grid(row=16, column=0, columnspan=2, sticky="w")
        ttk.Separator(setup).grid(row=17, column=0, columnspan=3, sticky="ew", pady=20)
        ttk.Label(setup, text="Application updates", style="Title.TLabel").grid(row=18, column=0, columnspan=2, sticky="w")
        self.update_message = ttk.Label(setup, text=f"Installed build: {build_summary()}", wraplength=850)
        self.update_message.grid(row=19, column=0, columnspan=2, sticky="w", pady=(10, 6))
        self.update_button = ttk.Button(setup, text="Check for updates", command=self.check_for_updates)
        self.update_button.grid(row=20, column=0, columnspan=2, sticky="w")
        ttk.Label(setup, text="An update replaces program files only. Settings and entries waiting to sync stay on this computer.",
                  wraplength=850).grid(row=21, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Separator(setup).grid(row=22, column=0, columnspan=3, sticky="ew", pady=20)
        ttk.Label(setup, text="Opening inventory and Excel migration", style="Title.TLabel").grid(row=23, column=0, columnspan=2, sticky="w")
        ttk.Label(setup, text="Import starting quantities and existing history into an empty database while the host is running.\nChoose a verified opening balance date. Entries on/before that date are archived; later entries change stock.", wraplength=850).grid(row=24, column=0, columnspan=2, sticky="w", pady=12)
        ttk.Button(setup, text="Open Excel import…", command=self.import_dialog).grid(row=25, column=0, columnspan=2, sticky="w")
        ttk.Label(setup, text="Form fields, required fields and staff dropdowns are managed centrally in server.json.\nManufacturer, implant type and component choices can be extended in client.json.\nWindows keeps the local queue and cache encrypted for the current Windows user.", wraplength=850).grid(row=26, column=0, columnspan=2, sticky="w", pady=30)
        setup.columnconfigure(1, weight=1)

    def check_for_updates(self, silent=False):
        self.update_button.configure(state="disabled")
        if not silent:
            self.update_message.configure(text="Checking GitHub for the latest Windows release…")

        def success(release):
            self.available_release = release
            if release["available"]:
                self.update_message.configure(text=f"Update available: {release['name']}")
                self.update_button.configure(text="Download and install update", command=self.install_update,
                                             state="normal")
            else:
                self.update_message.configure(text=f"Implant Inventory is up to date ({build_summary()}).")
                self.update_button.configure(text="Check again", command=self.check_for_updates, state="normal")

        def failure(error):
            self.update_button.configure(text="Check again", command=self.check_for_updates, state="normal")
            if not silent:
                self.update_message.configure(text=f"Could not check for updates: {error}")

        self.background(latest_release, success, failure)

    def install_update(self):
        release = getattr(self, "available_release", None)
        if not release:
            return self.check_for_updates()
        if not messagebox.askyesno(
                "Install update",
                "Implant Inventory will download the update, close, install it, and reopen.\n\n"
                "Entries waiting to sync and settings will be kept.", parent=self):
            return
        self.update_button.configure(state="disabled")
        self.update_message.configure(text="Downloading update…")

        def work():
            source = download_release(release, self.config_path.parent / "updates")
            launch_installer(source, self.config_path)
            return source

        def success(_):
            self.update_message.configure(text="Installer started. Closing Implant Inventory…")
            self.after(250, self.close)

        def failure(error):
            self.update_message.configure(text=f"Update was not installed: {error}")
            self.update_button.configure(state="normal")
            messagebox.showerror("Update failed", str(error), parent=self)

        self.background(work, success, failure)

    def save_connection(self):
        if self.syncing:
            return messagebox.showinfo("Sync in progress", "Wait for the current connection attempt to finish, then save.", parent=self)
        try:
            config = {**self.config, **{k: v.get().strip() for k, v in self.connection_vars.items()}}
            transport = Transport(config)
            if not config["exchange_folder"]:
                raise ValueError("Choose the shared Inventory Exchange folder")
            transport.expected_host_id = self.store.snapshot().get("host_id")
            save_config(self.config_path, config)
            self.config = config
            self.engine.transport = transport
            self.connection_message.configure(text="Settings saved.")
            self.start_sync()
        except Exception as error:
            messagebox.showerror("Invalid settings", str(error), parent=self)

    def browse_inventory_csv(self):
        path = filedialog.asksaveasfilename(parent=self, title="Choose the automatic inventory CSV",
                                            defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if path:
            self.export_vars["inventory_csv_path"].set(path)

    def browse_csv_backup_folder(self):
        path = filedialog.askdirectory(parent=self, title="Choose the dated CSV backup folder")
        if path:
            self.export_vars["csv_backup_directory"].set(path)

    def save_export_settings(self):
        settings = {key: variable.get().strip() for key, variable in self.export_vars.items()}
        self.export_message.configure(text="Saving settings on the inventory host…")
        def success(result):
            message = ("Host export settings saved. A CSV is written whenever inventory changes."
                       if not result.get("warning") else
                       "Settings saved, but the first export failed. The host will retry: " + result["warning"])
            self.export_message.configure(text=message)
            for key in self.export_vars:
                self.export_vars[key].set(result.get(key, ""))
        def failure(error):
            self.export_message.configure(text="Host export settings were not saved.")
            messagebox.showerror("Export settings failed", str(error), parent=self)
        self.background(lambda: self.engine.transport.post("/export_settings", settings), success, failure)

    def update_reorder_level(self, row, value):
        try:
            level = integer(value)
        except Exception as error:
            return messagebox.showerror("Invalid reorder level", str(error), parent=self)
        data = {key: row.get(key, "") for key in COMPONENT_FIELDS}
        data.update(ref_id=row["ref_id"], reorder_level=level, revision=row["revision"])
        self.status.configure(text=f"Saving reorder level for {row['ref_id']}…")
        def success(_result):
            self.status.configure(text=f"Reorder level saved for {row['ref_id']}")
            self.start_sync()
        def failure(error):
            self.status.configure(text="Could not save reorder level")
            messagebox.showerror("Reorder level not saved", str(error), parent=self)
        self.background(lambda: self.engine.transport.post("/components", data), success, failure)

    def staff_directory_dialog(self):
        window = tk.Toplevel(self)
        window.title("Manage clinical name directories")
        window.transient(self)
        window.geometry("760x560")
        container = ttk.Frame(window, padding=15)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="Clinical name directories", style="Title.TLabel").pack(anchor="w")
        ttk.Label(container, text="Changes are saved on the inventory host and appear on every computer after sync.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 10))
        tabs = ttk.Notebook(container)
        tabs.pack(fill="both", expand=True)
        labels = {"periodontist": "Periodontists", "referring_dentist": "Referring Dentists",
                  "assistant": "Assistants"}
        definitions = {field["key"]: field for field in self.fields.get("usage", [])}
        lists, entries, notices = {}, {}, {}

        def fill(key, choices):
            widget = lists[key]
            widget.delete(0, "end")
            for name in sorted(choices, key=str.casefold):
                widget.insert("end", name)

        def apply_result(key, result):
            fill(key, result["choices"])
            notices[key].configure(text=f"Saved {len(result['choices'])} names on the host.")
            entries[key].delete(0, "end")
            self.start_sync()

        def change(key, action, values):
            values = [value for value in values if value.strip()]
            if not values:
                return
            notices[key].configure(text="Saving on the inventory host…")
            def failure(error):
                notices[key].configure(text="Change was not saved.")
                messagebox.showerror("Directory change failed", str(error), parent=window)
            self.background(
                lambda: self.engine.transport.post(
                    "/staff_choices", {"field": key, "action": action, "values": values}),
                lambda result: apply_result(key, result), failure)

        for key in ("periodontist", "referring_dentist", "assistant"):
            frame = ttk.Frame(tabs, padding=12)
            tabs.add(frame, text=labels[key])
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            listbox = tk.Listbox(frame, selectmode="extended", exportselection=False,
                                 font=("Segoe UI", 11))
            listbox.grid(row=0, column=0, sticky="nsew")
            scroll = ttk.Scrollbar(frame, orient="vertical", command=listbox.yview)
            scroll.grid(row=0, column=1, sticky="ns")
            listbox.configure(yscrollcommand=scroll.set)
            lists[key] = listbox
            entry = ttk.Entry(frame)
            entry.grid(row=1, column=0, sticky="ew", pady=(10, 4))
            entries[key] = entry
            actions = ttk.Frame(frame)
            actions.grid(row=2, column=0, columnspan=2, sticky="ew")
            ttk.Button(actions, text="Add name",
                       command=lambda k=key: change(k, "add", [entries[k].get()])).pack(side="left")
            ttk.Button(actions, text="Remove selected",
                       command=lambda k=key: change(
                           k, "remove", [lists[k].get(i) for i in lists[k].curselection()])).pack(
                               side="left", padx=6)
            if key == "referring_dentist":
                ttk.Button(actions, text="Import from CSV…",
                           command=lambda: self.import_referring_dentists(window, change)).pack(side="right")
            notice = ttk.Label(frame, text="", style="Muted.TLabel")
            notice.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
            notices[key] = notice
            fill(key, definitions.get(key, {}).get("choices", []))
            entry.bind("<Return>", lambda _event, k=key: change(k, "add", [entries[k].get()]))

    def import_referring_dentists(self, parent, change):
        path = filedialog.askopenfilename(parent=parent, title="Choose a referring dentist CSV",
                                          filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, newline="", encoding="utf-8-sig") as stream:
                headers = next(csv.reader(stream), None)
            if not headers:
                raise ValueError("The CSV does not contain a header row")
        except (OSError, UnicodeError, csv.Error, ValueError) as error:
            return messagebox.showerror("CSV could not be opened", str(error), parent=parent)
        chooser = tk.Toplevel(parent)
        chooser.title("Select the Referring Dentist column")
        chooser.transient(parent)
        form = ttk.Frame(chooser, padding=18)
        form.pack(fill="both", expand=True)
        ttk.Label(form, text="Which column contains the Referring Dentist?",
                  style="Title.TLabel").pack(anchor="w")
        column = ttk.Combobox(form, values=headers, state="readonly", width=48)
        column.pack(fill="x", pady=12)
        column.current(0)
        ttk.Label(form, text="Blank cells and matching names are automatically skipped.",
                  style="Muted.TLabel").pack(anchor="w")
        def import_selected():
            try:
                values = csv_column_values(path, column.current())
            except (OSError, UnicodeError, csv.Error, ValueError) as error:
                return messagebox.showerror("CSV import failed", str(error), parent=chooser)
            chooser.destroy()
            change("referring_dentist", "add", values)
        ttk.Button(form, text="Import selected column", style="Accent.TButton",
                   command=import_selected).pack(anchor="e", pady=(18, 0))

    def component_editor(self, row=None):
        window = tk.Toplevel(self)
        window.title("Edit component" if row else "Add component")
        window.transient(self)
        form = ttk.Frame(window, padding=20)
        form.pack(fill="both", expand=True)
        values = {}
        columns = [("ref_id", "REF ID"), ("manufacturer", "Manufacturer"), ("implant_type", "Implant type"),
                   ("size", "Size"), ("component_type", "Component type"), ("other_parts", "Other / unique parts"),
                   ("reorder_level", "Reorder at")]
        if not row:
            columns += [("opening_quantity", "Opening quantity"), ("opening_date", "Opening date (end of day)")]
        for index, (key, label) in enumerate(columns):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", padx=(0, 15), pady=6)
            default = (date.today()-timedelta(days=1)).isoformat() if key == "opening_date" else 0 if key in ("opening_quantity", "reorder_level") else ""
            variable = tk.StringVar(value=(row or {}).get(key, default))
            values[key] = variable
            choices = self.config.get(key + "_choices")
            widget = ttk.Combobox(form, textvariable=variable, values=choices, width=43) if choices else ttk.Entry(form, textvariable=variable, width=46)
            if row and key == "ref_id":
                widget.configure(state="readonly")
            widget.grid(row=index, column=1, pady=6)
        button = ttk.Button(form, text="Save component")
        button.grid(row=len(columns), column=1, sticky="e", pady=14)
        def save():
            data = {key: value.get() for key, value in values.items()}
            if row:
                data["revision"] = row["revision"]
            button.configure(state="disabled")
            def success(_):
                window.destroy()
                self.start_sync()
            def failure(error):
                button.configure(state="normal")
                messagebox.showerror("Component not confirmed", str(error) + "\nIf delivery is pending, sync before making another change. Retrying these same values is safe.", parent=window)
            self.background(lambda: self.engine.transport.post("/components", data), success, failure)
        button.configure(command=save)

    def import_dialog(self):
        window = tk.Toplevel(self)
        window.title("Excel migration • review before import")
        window.geometry("1140x760")
        form = ttk.Frame(window, padding=15)
        form.pack(fill="x")
        variables = {}
        for index, (key, label) in enumerate((("dashboard", "Inventory / balance workbook"), ("log", "Supply log workbook (optional)"), ("profile", "Custom mapping JSON (optional)"), ("cutoff", "Opening date (YYYY-MM-DD)"))):
            variable = tk.StringVar()
            variables[key] = variable
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", pady=4, padx=(0, 10))
            ttk.Entry(form, textvariable=variable, width=72).grid(row=index, column=1, sticky="ew", pady=4)
            if key != "cutoff":
                def choose(k=key):
                    path = filedialog.askopenfilename(parent=window, filetypes=[("JSON", "*.json")] if k == "profile" else [("Excel", "*.xlsx *.xlsm")])
                    if path:
                        variables[k].set(path)
                ttk.Button(form, text="Browse…", command=choose).grid(row=index, column=2, padx=6)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Default mapping reads Base Supply (row 4), reorder levels from Inventory Full Supply (row 5), and Component Change Log (row 5).", wraplength=1050).grid(row=4, column=0, columnspan=3, sticky="w", pady=8)
        tabs = ttk.Notebook(window)
        tabs.pack(fill="both", expand=True, padx=15)
        report_frame = ttk.Frame(tabs)
        tabs.add(report_frame, text="Validation report")
        report_text = tk.Text(report_frame, wrap="word", height=10)
        report_text.pack(fill="both", expand=True)
        preview = DataTable(tabs, [("ref_id", "REF ID"), ("manufacturer", "Manufacturer"), ("implant_type", "Implant type"),
                                  ("size", "Size"), ("opening_quantity", "Opening quantity"), ("change", "Later change"), ("projected", "Projected quantity")])
        tabs.add(preview, text="Quantity preview")
        history = DataTable(tabs, LOG_COLUMNS)
        tabs.add(history, text="History preview")
        bottom = ttk.Frame(window, padding=15)
        bottom.pack(fill="x")
        confirmed = tk.BooleanVar(value=False)
        ttk.Checkbutton(bottom, text="I verified opening quantities at the END of the cutoff date; older history is already included.", variable=confirmed).pack(anchor="w", pady=6)
        state = {}
        import_button = ttk.Button(bottom, text="Import reviewed data", state="disabled")
        import_button.pack(side="right")
        def preview_data():
            arguments = {key: value.get().strip() for key, value in variables.items()}
            import_button.configure(state="disabled")
            confirmed.set(False)
            def work():
                profile = json.loads(Path(arguments["profile"]).read_text(encoding="utf-8")) if arguments["profile"] else None
                return prepare(arguments["dashboard"], arguments["log"] or None, arguments["cutoff"], profile)
            def success(result):
                bundle, report = result
                state.update(bundle=bundle, report=report, arguments=arguments)
                report_text.delete("1.0", "end")
                report_text.insert("1.0", json.dumps(report, indent=2, ensure_ascii=False))
                changes = {}
                for event in bundle["events"]:
                    if event["date"] > bundle["cutoff"]:
                        changes[event["ref_id"]] = changes.get(event["ref_id"], 0) + event["quantity"] * (-1 if event["kind"] == "usage" else 1)
                preview.set_rows([{**row, "change": changes.get(row["ref_id"], 0),
                                  "projected": row["opening_quantity"] + changes.get(row["ref_id"], 0)} for row in bundle["components"]])
                history.set_rows([{**event, "affects_stock": int(event["date"] > bundle["cutoff"])} for event in bundle["events"]])
                import_button.configure(state="normal" if report["ready"] else "disabled")
            self.background(work, success)
        def import_data():
            if not confirmed.get():
                return messagebox.showinfo("Verify opening inventory", "Review the quantities, history and warnings, then check the verification box.", parent=window)
            if {k: v.get().strip() for k, v in variables.items()} != state.get("arguments"):
                return messagebox.showinfo("Preview needed", "The import settings changed. Preview again before importing.", parent=window)
            import_button.configure(state="disabled")
            def success(result):
                messagebox.showinfo("Migration complete", "The reviewed opening inventory and history were imported successfully.", parent=window)
                window.destroy()
                self.start_sync()
            def failure(error):
                import_button.configure(state="normal")
                messagebox.showerror("Migration not confirmed", str(error) + "\nRetry the same reviewed import if the connection failed; it is safe to retry.", parent=window)
            self.background(lambda: self.engine.transport.post("/import", state["bundle"]), success, failure)
        import_button.configure(command=import_data)
        ttk.Button(bottom, text="Preview and validate", command=preview_data).pack(side="left")

    def close(self):
        self.closing = True
        for callback in (getattr(self, "poll_after", None),
                         getattr(self, "initial_sync_after", None),
                         getattr(self, "periodic_after", None),
                         getattr(self, "update_after", None)):
            if callback:
                try:
                    self.after_cancel(callback)
                except tk.TclError:
                    pass  # The callback may already have run.
        self.destroy()  # Every entry was committed before the form displayed 'saved'.


def main():
    parser = argparse.ArgumentParser(description="Implant Inventory desktop")
    parser.add_argument("--config", help="Client config in a local directory; defaults to LocalAppData")
    args = parser.parse_args()
    # One application instance per Windows user prevents two sync engines sharing one outbox.
    mutex = None
    if os.name == "nt":
        import ctypes
        import hashlib
        from ctypes import wintypes
        name = hashlib.sha256(str(Path(args.config or data_directory() / 'client.json').resolve()).encode()).hexdigest()[:24]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        mutex = kernel.CreateMutexW(None, False, "Local\\ImplantInventory-" + name)
        if not mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("Implant Inventory", "Implant Inventory is already open. Switch to its existing window.")
            root.destroy()
            return
    try:
        App(args.config).mainloop()
    except Exception as error:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Implant Inventory could not start", str(error))
        root.destroy()
    finally:
        if mutex:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(mutex)


if __name__ == "__main__":
    main()
