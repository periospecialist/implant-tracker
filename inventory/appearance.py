"""Compact standard ttk controls with light/dark colors; no inventory logic."""
import tkinter as tk
from tkinter import font, ttk

PALETTES = {
    'Light': dict(background='#f2f2f7', surface='#ffffff', field='#f5f5f9',
                  text='#1c1c1e', muted='#63636b', border='#d1d1d8',
                  accent='#0066cc', hover='#005bb8', pressed='#004e9c', tint='#e5f0ff'),
    'Dark': dict(background='#000000', surface='#1c1c1e', field='#2c2c2e',
                 text='#f5f5f7', muted='#aeaeb5', border='#48484a',
                 accent='#0a84ff', hover='#3299ff', pressed='#0066cc', tint='#163250'),
}
# Points for typography; logical pixels for compact spacing.
SPACING = dict(page=10, card=10, gap=6, control=4, radius=0)


class Appearance:
    def __init__(self, root, mode='Light'):
        self.root = root
        families = set(font.families(root))
        self.family = next((f for f in ('Segoe UI', 'DejaVu Sans')
                            if f in families), font.nametofont('TkDefaultFont').actual('family'))
        for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont'):
            font.nametofont(name).configure(family=self.family, size=12)
        self.style = ttk.Style(root)
        for name, colors in PALETTES.items():
            theme = 'Inventory' + name
            if theme not in self.style.theme_names():
                self.style.theme_create(theme, parent='clam')
            self.style.theme_use(theme)
            self._build(colors)
        self.apply(mode)
        # Newly opened dialogs and pop-up menus inherit the current palette.
        root.bind_all('<Map>', self._mapped, add='+')

    def _build(self, c):
        s = self.style
        s.configure('.', font=(self.family, 12), background=c['surface'], foreground=c['text'],
                    bordercolor=c['border'], troughcolor=c['background'], selectbackground=c['accent'],
                    selectforeground='white', insertcolor=c['text'])
        s.configure('TFrame', background=c['surface'])
        s.configure('Chrome.TFrame', background=c['background'])
        s.configure('TLabel', background=c['surface'], foreground=c['text'])
        s.configure('Chrome.TLabel', background=c['background'], foreground=c['muted'])
        s.configure('Muted.TLabel', foreground=c['muted'])
        s.configure('Title.TLabel', font=(self.family, 14, 'bold'), foreground=c['text'])
        s.configure('TNotebook', background=c['background'], borderwidth=1, padding=2, tabmargins=(0, 0, 0, 0))
        s.configure('TNotebook.Tab', padding=(8, 4), background=c['background'], foreground=c['muted'])
        s.map('TNotebook.Tab', background=[('selected', c['surface']), ('active', c['field'])],
              foreground=[('selected', c['accent']), ('active', c['text'])])
        for name in ('TButton', 'Accent.TButton'):
            s.configure(name, padding=(8, 4), background=c['field'], foreground=c['text'],
                        borderwidth=1, relief='raised', anchor='center')
            s.map(name, background=[('pressed', c['tint']), ('active', c['field'])],
                  foreground=[('disabled', c['muted'])],
                  relief=[('pressed', 'sunken'), ('!pressed', 'raised')])
        for name in ('TEntry', 'TCombobox', 'TMenubutton'):
            s.configure(name, padding=3, fieldbackground=c['field'], background=c['field'],
                        foreground=c['text'], arrowcolor=c['muted'], borderwidth=1,
                        lightcolor=c['border'], darkcolor=c['border'])
            s.map(name, bordercolor=[('focus', c['accent'])],
                  fieldbackground=[('readonly', c['field']), ('disabled', c['background'])],
                  foreground=[('disabled', c['muted']), ('readonly', c['text'])],
                  background=[('active', c['tint']), ('readonly', c['field'])])
        s.configure('TCheckbutton', padding=2)
        s.map('TCheckbutton', background=[('active', c['field'])])
        s.configure('Lookup.TLabel', background=c['field'], foreground=c['muted'], padding=6)
        s.configure('Verified.Lookup.TLabel', background=c['tint'], foreground=c['text'], padding=6)
        s.configure('Treeview', rowheight=28, background=c['surface'], fieldbackground=c['surface'],
                    foreground=c['text'], borderwidth=0, font=(self.family, 12))
        s.map('Treeview', background=[('selected', c['tint'])], foreground=[('selected', c['text'])])
        s.configure('Treeview.Heading', background=c['field'], foreground=c['muted'],
                    font=(self.family, 11, 'bold'), padding=(6, 4), relief='raised')
        s.map('Treeview.Heading', background=[('active', c['tint'])])
        s.configure('Inventory.Treeview', font=(self.family, 12, 'bold'))
        s.configure('Inventory.Treeview.Heading', font=(self.family, 12, 'bold'))
        s.configure('Reorder.Treeview', font=(self.family, 12, 'bold'))
        s.configure('Reorder.Treeview.Heading', background='#243244', foreground='white',
                    font=(self.family, 12, 'bold'))
        # Preserve the status backgrounds even when a row is selected.
        s.map('Reorder.Treeview', background=[], foreground=[])
        s.map('Reorder.Treeview.Heading', background=[('active', '#33465e')], foreground=[('active', 'white')])
        s.configure('ActiveFilter.TButton', background=c['accent'], foreground='white')
        s.map('ActiveFilter.TButton', background=[('active', c['hover'])], foreground=[('active', 'white')])
        s.configure('Filter.Toolbutton', padding=(8, 5), background=c['field'], foreground=c['text'],
                    font=(self.family, 12), relief='raised')
        s.map('Filter.Toolbutton', background=[('selected', c['accent']), ('active', c['tint'])],
              foreground=[('selected', 'white')], relief=[('selected', 'sunken')])
        s.configure('TScrollbar', background=c['border'], arrowcolor=c['muted'], borderwidth=0)

    def apply(self, mode):
        self.mode = mode if mode in PALETTES else 'Light'
        self.colors = PALETTES[self.mode]
        self.style.theme_use('Inventory' + self.mode)
        self.root.configure(background=self.colors['background'])
        for option, value in (('*TCombobox*Listbox.background', self.colors['field']),
                              ('*TCombobox*Listbox.foreground', self.colors['text']),
                              ('*TCombobox*Listbox.selectBackground', self.colors['accent']),
                              ('*TCombobox*Listbox.selectForeground', 'white')):
            self.root.option_add(option, value)
        self._walk(self.root)

    def _mapped(self, event):
        self._paint(event.widget)

    def _walk(self, widget):
        self._paint(widget)
        for child in widget.winfo_children():
            self._walk(child)

    def _paint(self, widget):
        c = self.colors
        if isinstance(widget, (tk.Canvas, tk.Toplevel)):
            widget.configure(background=c['surface'])
        elif isinstance(widget, (tk.Text, tk.Listbox)):
            widget.configure(background=c['field'], foreground=c['text'],
                             selectbackground=c['accent'], selectforeground='white',
                             highlightbackground=c['border'], highlightcolor=c['accent'])
            if isinstance(widget, tk.Text):
                widget.configure(insertbackground=c['text'])
        elif isinstance(widget, tk.Menu):
            widget.configure(background=c['surface'], foreground=c['text'],
                             activebackground=c['tint'], activeforeground=c['text'])
