STYLE = '''
* { font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 13px; color: #252936; }
QMainWindow, QWidget#root { background: #f5f6f8; }
QFrame#sidebar { background: #ffffff; border-right: 1px solid #e9ebef; }
QFrame#card { background: #ffffff; border: 1px solid #e7e9ed; border-radius: 16px; }
QFrame#bar { background: #ffffff; border: 1px solid #e7e9ed; border-radius: 14px; }
QLabel#brand { font-size: 20px; font-weight: 700; }
QLabel#headline { font-size: 27px; font-weight: 700; color: #252936; }
QLabel#title { font-size: 16px; font-weight: 700; }
QLabel#muted { color: #82899b; font-size: 12px; }
QLabel#chip { color: #9b5325; background: #fff1df; border-radius: 10px; padding: 7px 12px; }
QLabel#avatar { background: #fff0e8; color: #e87245; border-radius: 18px; font-weight: 700; font-size: 16px; }
QPushButton { background: #fff; border: 1px solid #e0e3e9; border-radius: 8px; padding: 8px 12px; }
QPushButton:hover { background: #f6f7fa; border-color: #c8cfdb; }
QPushButton:pressed { background: #ebeef4; }
QPushButton:disabled { color: #acb2be; background: #f0f2f5; border-color: #e8ebef; }
QPushButton#primary { background: #f27145; color: white; border: 0; font-weight: 700; padding: 12px 20px; }
QPushButton#primary:hover { background: #e86337; }
QPushButton#primary:disabled { background: #f4c3b1; color: #fff; }
QPushButton#nav { text-align: left; padding: 12px 18px; background: transparent; border: none; border-radius: 10px; color: #6f788b; }
QPushButton#nav:checked { background: #fff0e9; color: #db663d; font-weight: 700; }
QPushButton#link { border: none; color: #cc643e; background: transparent; padding: 5px; }
QLineEdit, QTextEdit, QComboBox { background: #fafbfc; border: 1px solid #e2e5eb; border-radius: 9px; padding: 8px; selection-background-color: #f6b69b; }
QLineEdit:focus, QTextEdit:focus { border-color: #ef986f; background: #fff; }
QComboBox { min-height: 20px; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: #fff; selection-background-color: #fff0e9; color: #333; }
QTableWidget { background: #fff; border: none; gridline-color: #f0f1f4; selection-background-color: #fff4ed; selection-color: #252936; outline: none; }
QTableWidget::item { padding: 7px; border-bottom: 1px solid #f1f2f5; }
QHeaderView::section { background: #fafbfc; border: none; border-bottom: 1px solid #edf0f4; padding: 10px 6px; color: #9198a7; font-size: 11px; }
QTableWidget::indicator, QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #cad0da; border-radius: 5px; background: #fff; }
QTableWidget::indicator:checked, QCheckBox::indicator:checked { background: #f27145; border-color: #f27145; }
QTabWidget::pane { border: none; background: #fff; }
QTabBar::tab { background: #f5f6f8; color: #858c9c; padding: 10px 17px; border-radius: 7px; margin: 0 4px 12px 0; }
QTabBar::tab:selected { background: #fff0e9; color: #d9653c; font-weight: 700; }
QProgressBar { height: 5px; border: none; border-radius: 3px; background: #edf0f4; color: transparent; }
QProgressBar::chunk { background: #f27145; border-radius: 3px; }
QScrollBar:vertical { background: transparent; width: 7px; }
QScrollBar::handle:vertical { background: #d9dee6; border-radius: 3px; min-height: 35px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QDialog { background: #fff; }
QToolTip { background: #fff; border: 1px solid #ddd; padding: 5px; }
'''
