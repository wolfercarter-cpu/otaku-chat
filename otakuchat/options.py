# (label shown in the menu, slash command it actually runs) — kept as pairs
# rather than deriving one from the other since a couple of labels read
# better short than their real command name (e.g. "name" for /rename).
# Constructed into a picker via pickers.ListPicker (app.py's /menu handler).
MENU_ITEMS = [
    ("help", "/help"),
    ("model", "/model"),
    ("memory", "/memory"),
    ("facts", "/facts"),
    ("snippets", "/snippets"),
    ("vault", "/vault"),
    ("import", "/import"),
    ("functions", "/functions"),
    ("config", "/config"),
    ("new", "/new"),
    ("sessions", "/sessions"),
    ("name", "/rename"),
    ("export", "/export"),
    ("add", "/add"),
    ("think", "/think"),
    ("pattern", "/pattern"),
    ("pattern off", "/pattern off"),
    ("prompt", "/prompt"),
    ("leap", "/leap"),
    ("quit", "/quit"),
]
