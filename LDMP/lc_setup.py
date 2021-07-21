# -*- coding: utf-8 -*-
"""
/***************************************************************************
 LDMP - A QGIS plugin
 This plugin supports monitoring and reporting of land degradation to the UNCCD 
 and in support of the SDG Land Degradation Neutrality (LDN) target.
                              -------------------
        begin                : 2017-05-23
        git sha              : $Format:%H$
        copyright            : (C) 2017 by Conservation International
        email                : trends.earth@conservation.org
 ***************************************************************************/
"""

import json
import os
import typing
from pathlib import Path

from PyQt5 import (
    QtCore,
    QtGui,
    QtWidgets,
    uic
)

from qgis.utils import iface

from . import (
    conf,
    data_io,
)
from .layers import tr_style_text
from .logger import log

DlgCalculateLCSetAggregationUi, _ = uic.loadUiType(
    str(Path(__file__).parent / "gui/DlgCalculateLCSetAggregation.ui"))
WidgetLcDefineDegradationUi, _ = uic.loadUiType(
    str(Path(__file__).parent / "gui/WidgetLCDefineDegradation.ui"))

WidgetLandCoverSetupLocalExecutionUi, _ = uic.loadUiType(
    str(Path(__file__).parent / "gui/land_cover_setup_widget_local.ui"))
WidgetLandCoverSetupRemoteExecutionUi, _ = uic.loadUiType(
    str(Path(__file__).parent / "gui/land_cover_setup_widget.ui"))

mb = iface.messageBar()


from te_schemas.land_cover import (
        LCLegend, LCLegendNesting,
        LCTransitionMeaningDeg,
        LCTransitionDefinitionDeg)

from marshmallow.exceptions import ValidationError
class tr_lc_setup(object):
    def tr(message):
        return QtCore.QCoreApplication.translate("tr_lc_setup", message)

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    #TODO: This was QPyNullVariant under pyqt4 - check the below works on pyqt5
    if isinstance(obj, QtCore.QJsonValue.Null):
        return None
    raise TypeError("Type {} not serializable".format(type(obj)))


class VerticalLabel(QtWidgets.QLabel):
    def __init__(self, parent=None):
        super(VerticalLabel, self).__init__(parent)

    def paintEvent(self, paint_event):
        painter = QtGui.QPainter(self)
        painter.translate(self.sizeHint().width(), self.sizeHint().height())
        painter.rotate(270)
        painter.drawText(0, 0, self.text())

    def minimumSizeHint(self):
        s = QtWidgets.QLabel.minimumSizeHint(self)
        return QtCore.QSize(s.height(), s.width())

    def sizeHint(self):
        s = QtWidgets.QLabel.sizeHint(self)
        return QtCore.QSize(s.height(), s.width())


class TransMatrixEdit(QtWidgets.QLineEdit):
    def __init__(self, parent=None):
        super(TransMatrixEdit, self).__init__(parent)

        self.textChanged.connect(self.transition_cell_changed)

    def transition_cell_changed(self, text):
        if self.text() == '-':
            self.setStyleSheet('QLineEdit {background: #AB2727;} QLineEdit:hover {border: 1px solid gray; background: #AB2727;}')
        elif self.text() == '+':
            self.setStyleSheet('QLineEdit {background: #45A146;} QLineEdit:hover {border: 1px solid gray; background: #45A146;}')
        else:
            self.setStyleSheet('QLineEdit {background: #FFFFE0;} QLineEdit:hover {border: 1px solid gray; background: #FFFFE0;}')

    def focusInEvent(self, e):
        super(TransMatrixEdit, self).focusInEvent(e)
        self.selectAll()


class LCClassComboBox(QtWidgets.QComboBox):
    def __init__(self, nesting, parent=None, *args):
        super(LCClassComboBox, self).__init__(parent)
        self.nesting = nesting

        # Add the translations of the item labels in order of their codes
        self.addItems([c.name_long for c in self.nesting.parent.orderByCode().key])

        for n in range(0, len(nesting.parent.key)):
            color = self.nesting.parent.classByNameLong(self.itemData(n, QtCore.Qt.DisplayRole)).color
            self.setItemData(n, QtGui.QColor(color), QtCore.Qt.BackgroundRole)
            if color == '#000000':
                self.setItemData(n, QtGui.QColor('#FFFFFF'), QtCore.Qt.ForegroundRole)
            else:
                self.setItemData(n, QtGui.QColor('#000000'), QtCore.Qt.ForegroundRole)

        self.index_changed()
        self.currentIndexChanged.connect(self.index_changed)

    def index_changed(self):
        color = self.nesting.parent.classByNameLong(self.currentText()).color
        if color == '#000000':
            self.setStyleSheet('QComboBox:editable {{background-color: {}; color: #FFFFFF;}}'.format(color))
        else:
            self.setStyleSheet('QComboBox:editable {{background-color: {};}}'.format(color))

class LCAggTableModel(QtCore.QAbstractTableModel):
    def __init__(self, nesting, parent=None, *args):
        QtCore.QAbstractTableModel.__init__(self, parent, *args)
        self.nesting = nesting
        
        # Column names as tuples with json name in [0], pretty name in [1]
        # Note that the columns with json names set to to INVALID aren't loaded
        # into the shell, but shown from a widget.
        colname_tuples = [('Child_Code', tr_lc_setup.tr('Input code')),
                          ('Child_Label', tr_lc_setup.tr('Input class')),
                          ('Parent_Label', tr_lc_setup.tr('Output class'))]
        self.colnames_json = [x[0] for x in colname_tuples]
        self.colnames_pretty = [x[1] for x in colname_tuples]

    def rowCount(self, parent):
        return len(self.nesting.child.key)

    def columnCount(self, parent):
        return len(self.colnames_json)

    def data(self, index, role):
        if not index.isValid():
            return None
        elif role == QtCore.Qt.TextAlignmentRole and index.column() in [0, 2, 3]:
            return QtCore.Qt.AlignCenter
        elif role != QtCore.Qt.DisplayRole:
            return None
        col_name = self.colnames_json[index.column()]
        initial_class = self.nesting.child.key[index.row()]
        if col_name == 'Child_Code':
            return initial_class.code
        elif col_name == 'Child_Label':
            return initial_class.name_long
        elif col_name == 'Parent_Label':
            return self.nesting.parentClassForChild(initial_class).name_long

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if role == QtCore.Qt.DisplayRole and orientation == QtCore.Qt.Horizontal:
            return self.colnames_pretty[section]
        return QtCore.QAbstractTableModel.headerData(self, section, orientation, role)


def read_lc_nesting_file(f):
    if not os.access(f, os.R_OK):
        QtWidgets.QMessageBox.critical(None,
                tr_lc_setup.tr("Error"),
                tr_lc_setup.tr(u"Cannot read {}.".format(f)))
        return None

    try:
        with open(f) as nesting_file:
            nesting = LCLegendNesting.Schema().loads(nesting_file.read())
    except ValidationError as e:
        log(u'Error loading land cover legend nesting definition from {}: {}'.format(f, e))
        QtWidgets.QMessageBox.critical(None,
                                       tr_lc_setup.tr("Error"),
                                       tr_lc_setup.tr("{} does not appear to contain a valid land cover legend nesting definition: {}".format(f, e)))
        return None
    else:
        log(u'Loaded land cover legend nesting definition from {}'.format(f))
        return nesting 


def read_lc_matrix_file(f):
    if not os.access(f, os.R_OK):
        QtWidgets.QMessageBox.critical(None,
                tr_lc_setup.tr("Error"),
                tr_lc_setup.tr(u"Cannot read {}.".format(f)))
        return None

    try:
        with open(f) as matrix_file:
            matrix = LCTransitionDefinitionDeg.Schema().loads(matrix_file.read())
    except ValidationError as e:
        log(u'Error loading land cover transition matrix from {}: {}'.format(f, e))
        QtWidgets.QMessageBox.critical(None,
                                       tr_lc_setup.tr("Error"),
                                       tr_lc_setup.tr("{} does not appear to contain a valid land cover transition matrix definition: {}".format(f, e)))
        return None
    else:
        log(u'Loaded land cover transition matrix definition from {}'.format(f))
        return matrix


def get_lc_nesting():
    nesting = QtCore.QSettings().value("LDMP/land_cover_nesting", None)
    if nesting is None:
        nesting = read_lc_nesting_file(os.path.join(os.path.dirname(os.path.realpath(__file__)),
            'data', 'land_cover_nesting_UNCCD_ESA.json'))
        QtCore.QSettings().setValue("LDMP/land_cover_nesting", LCLegendNesting.Schema().dumps(nesting))
    else:
        nesting = LCLegendNesting.Schema().loads(nesting)
    return nesting


def get_trans_matrix():
    matrix = QtCore.QSettings().value("LDMP/land_cover_transition_matrix", None)
    matrix = None
    if matrix is None:
        matrix = read_lc_matrix_file(os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                     'data', 'land_cover_transition_matrix_UNCCD.json'))
        if matrix:
            QtCore.QSettings().setValue("LDMP/land_cover_transition_matrix", LCTransitionDefinitionDeg.Schema().dumps(matrix))
    else:
        matrix = LCTransitionDefinitionDeg.Schema().loads(matrix)
    return matrix


class DlgCalculateLCSetAggregation(QtWidgets.QDialog, DlgCalculateLCSetAggregationUi):
    def __init__(self, nesting, parent=None):
        super().__init__(parent)

        self.nesting = nesting

        self.setupUi(self)

        self.btn_save.clicked.connect(self.btn_save_pressed)
        self.btn_load.clicked.connect(self.btn_load_pressed)
        self.btn_reset.clicked.connect(self.reset_class_table)
        self.btn_close.clicked.connect(self.btn_close_pressed)

        # Setup the class table so that the table is defined when a user first 
        # loads the dialog
        self.reset_class_table()

    def btn_close_pressed(self):
        self.close()

    def btn_load_pressed(self):
        f, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            self.tr('Select a land cover definition file'),
            conf.settings_manager.get_value(conf.Setting.DEFINITIONS_DIRECTORY),
            self.tr('Land cover definition (*.json)')
        )
        if f:
            if os.access(f, os.R_OK):
                conf.settings_manager.write_value(
                    conf.Setting.DEFINITIONS_DIRECTORY, os.path.dirname(f))
            else:
                QtWidgets.QMessageBox.critical(
                    None,
                    self.tr("Error"),
                    self.tr(u"Cannot read {}. Choose a different file.".format(f))
                )
        else:
            return
        nesting = read_lc_nesting_file(f)

        if nesting:
            self.nesting = nesting
            self.setup_class_table(nesting)

    def btn_save_pressed(self):
        f, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            self.tr('Choose where to save this land cover definition'),
            conf.settings_manager.get_value(conf.Setting.DEFINITIONS_DIRECTORY),
            self.tr('Land cover definition (*.json)')
        )
        if f:
            if os.access(os.path.dirname(f), os.W_OK):
                conf.settings_manager.write_value(
                    conf.Setting.DEFINITIONS_DIRECTORY, os.path.dirname(f))
            else:
                QtWidgets.QMessageBox.critical(
                    None,
                    self.tr("Error"),
                    self.tr(u"Cannot write to {}. Choose a different file.".format(f)))
                return

            with open(f, 'w') as outfile:
                json.dump(LCLegendNesting.Schema().dump(self.nesting), 
                          outfile, sort_keys=True, indent=4,
                          separators=(',', ':'), default=json_serial)


    def setup_class_table(self, nesting=None):
        # Load the codes each class will be recoded to.
        # 
        # The "nesting" parameter will include any mappings derived from a 
        # class definition file, or, in the case or reading in user land cover 
        # files, nesting from the file itself.
        # 
        # The default codes stored in self.nesting are derived either 
        # from the data/land_cover_nesting_UNCCD_ESA.json file when this class 
        # is instantiated from the LCSetupWidget, or from the values within a 
        # custom user data file when this class is instantiated from the 
        # DlgDataIOImportLC class.
        if nesting:
            #TODO Fix loading of existing/new class definitions
            child_codes = sorted([c.code for c in nesting.child.key])
            default_codes = sorted([c.code for c in self.nesting.child.key])
            new_codes = [c for c in child_codes if c not in default_codes]
            missing_codes = [c for c in default_codes if c not in child_codes]
            if len(new_codes) > 0:
                QtWidgets.QMessageBox.warning(None,
                                              self.tr("Warning"),
                                              self.tr(u"Some of the class codes ({}) in the definition file do not appear in the chosen data file.".format(', '.join([str(c) for c in new_codes]))))
            if len(missing_codes) > 0:
                QtWidgets.QMessageBox.warning(None,
                                              self.tr("Warning"),
                                              self.tr(u"Some of the class codes ({}) in the data file do not appear in the chosen definition file.".format(', '.join([str(c) for c in missing_codes]))))

            # Setup a new nesting list with the new class codes for all classes 
            # included in default classes, and any other class codes that are 
            # missing added from the default class list
            nesting = [c for c in nesting if c['Child_Code'] in default_codes]
            nesting.extend([c for c in self.nesting if c['Child_Code'] not in child_codes])
        else:
            nesting = self.nesting

        table_model = LCAggTableModel(nesting, parent=self)
        proxy_model = QtCore.QSortFilterProxyModel()
        proxy_model.setSourceModel(table_model)
        self.remap_view.setModel(proxy_model)

        # Add selector in cell
        for row in range(0, len(nesting.child.key)):
            # Set the default final codes for each row. Note that the QComboBox 
            # entries are potentially translated, so need to link the 
            # translated names back to a particular code.
            
            # Get the input code for this row and the final label it should map 
            # to by default
            child_code = table_model.index(row, 0).data()
            parent_class = [nesting.parentClassForChild(c) for c in nesting.child.key if c.code == child_code][0]

            # Figure out which label translation this Parent_Label (in English) 
            # is equivalent to
            parent_label_tr = tr_style_text(parent_class.name_long)

            lc_class_combo = LCClassComboBox(nesting)

            # Find the index in the combo box of this translated final label
            ind = lc_class_combo.findText(parent_label_tr)
            if ind != -1:
                lc_class_combo.setCurrentIndex(ind)
            self.remap_view.setIndexWidget(proxy_model.index(row, 2), lc_class_combo)

        self.remap_view.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.remap_view.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.remap_view.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        
        self.remap_view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)

        return True

    def reset_class_table(self):
        self.setup_class_table()


class LCDefineDegradationWidget(QtWidgets.QWidget, WidgetLcDefineDegradationUi):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setupUi(self)

        self.nesting = get_lc_nesting()

        self.trans_matrix = get_trans_matrix()

        self.deg_def_matrix.setRowCount(len(self.trans_matrix.legend.key))
        self.deg_def_matrix.setColumnCount(len(self.trans_matrix.legend.key))
        self.deg_def_matrix.setHorizontalHeaderLabels([c.name_short for c in self.trans_matrix.legend.key])
        self.deg_def_matrix.setVerticalHeaderLabels([c.name_short for c in self.trans_matrix.legend.key])

        for row in range(0, self.deg_def_matrix.rowCount()):
            for col in range(0, self.deg_def_matrix.columnCount()):
                line_edit = TransMatrixEdit()
                line_edit.setValidator(QtGui.QRegExpValidator(QtCore.QRegExp("[-0+]")))
                line_edit.setAlignment(QtCore.Qt.AlignHCenter)
                self.deg_def_matrix.setCellWidget(row, col, line_edit)
        self.set_trans_matrix()

        # Setup the vertical label for the rows of the table
        label_lc_baseline_year = VerticalLabel(self)
        label_lc_baseline_year.setText(self.tr("Land cover in initial year "))
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Minimum)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.label_lc_target_year.sizePolicy().hasHeightForWidth())
        label_lc_baseline_year.setSizePolicy(sizePolicy)
        font = QtGui.QFont()
        font.setBold(True)
        font.setWeight(75)
        label_lc_baseline_year.setFont(font)
        self.lc_trans_table_layout.addWidget(label_lc_baseline_year, 1, 0, 1, 1, QtCore.Qt.AlignCenter)

        self.deg_def_matrix.setStyleSheet('QTableWidget {border: 0px;}')
        self.deg_def_matrix.horizontalHeader().setStyleSheet('QHeaderView::section {background-color: white;border: 0px;}')
        self.deg_def_matrix.verticalHeader().setStyleSheet('QHeaderView::section {background-color: white;border: 0px;}')

        for row in range(0, self.deg_def_matrix.rowCount()):
            self.deg_def_matrix.horizontalHeader().setSectionResizeMode(row, QtWidgets.QHeaderView.Stretch)
        for col in range(0, self.deg_def_matrix.columnCount()):
            self.deg_def_matrix.verticalHeader().setSectionResizeMode(col, QtWidgets.QHeaderView.Stretch)

        self.btn_transmatrix_reset.clicked.connect(self.set_trans_matrix)
        self.btn_transmatrix_loadfile.clicked.connect(self.trans_matrix_loadfile)
        self.btn_transmatrix_savefile.clicked.connect(self.trans_matrix_savefile)

        self.legend_deg.setStyleSheet('QLineEdit {background: #AB2727;} QLineEdit:hover {border: 1px solid gray; background: #AB2727;}')
        self.legend_imp.setStyleSheet('QLineEdit {background: #45A146;} QLineEdit:hover {border: 1px solid gray; background: #45A146;}')
        self.legend_stable.setStyleSheet('QLineEdit {background: #FFFFE0;} QLineEdit:hover {border: 1px solid gray; background: #FFFFE0;}')

    def trans_matrix_loadfile(self):
        f, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            self.tr('Select a transition matrix definition file'),
            conf.settings_manager.get_value(conf.Setting.DEFINITIONS_DIRECTORY),
            self.tr('Transition matrix definition (*.json)')
        )
        if f:
            if os.access(f, os.R_OK):
                conf.settings_manager.write_value(
                    conf.Setting.DEFINITIONS_DIRECTORY, os.path.dirname(f))
            else:
                QtWidgets.QMessageBox.critical(
                    None,
                    self.tr("Error"),
                    self.tr(u"Cannot read {}. Choose a different file.".format(f))
                )
        else:
            return None

        matrix = read_lc_matrix_file(f)
        if not matrix:
            return None
        else:
            self.set_trans_matrix(matrix)
            return True

    def trans_matrix_savefile(self):
        f, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            self.tr('Choose where to save this transition matrix definition'),
            conf.settings_manager.get_value(conf.Setting.DEFINITIONS_DIRECTORY),
            self.tr('Transition matrix definition (*.json)')
        )
        if f:
            if os.access(os.path.dirname(f), os.W_OK):
                conf.settings_manager.write_value(
                    conf.Setting.DEFINITIONS_DIRECTORY, os.path.dirname(f))
            else:
                QtWidgets.QMessageBox.critical(None,
                                               self.tr("Error"),
                                               self.tr(u"Cannot write to {}. Choose a different file.".format(f)))
                return

            with open(f, 'w') as outfile:
                json.dump(LCTransitionDefinitionDeg.Schema().dump(self.trans_matrix_get()),
                          outfile, sort_keys=True, indent=4,
                          separators=(',', ':'), default=json_serial)

    def set_trans_matrix(self, matrix=None):
        if matrix:
            QtCore.QSettings().setValue("LDMP/land_cover_transition_matrix", LCTransitionDefinitionDeg.Schema().dumps(matrix))
        else:
            matrix = get_trans_matrix()
        for row in range(0, self.deg_def_matrix.rowCount()):
            initial_class = matrix.legend.key[row]
            for col in range(0, self.deg_def_matrix.columnCount()):
                final_class = matrix.legend.key[col]
                meaning = matrix.definitions.meaningByTransition(initial_class, final_class)
                if meaning == 'stable':
                    code = '0'
                elif meaning == 'degradation':
                    code = '-'
                elif meaning == 'improvement':
                    code = '+'
                else:
                    log('unrecognized value "{}" when setting transition matrix'.format(meaning))
                    return False
                self.deg_def_matrix.cellWidget(row, col).setText(code)
        return True

    def trans_matrix_get(self):
        # Extract trans_matrix from the QTableWidget
        transitions = []
        for row in range(0, self.deg_def_matrix.rowCount()):
            for col in range(0, self.deg_def_matrix.columnCount()):
                val = self.deg_def_matrix.cellWidget(row, col).text()
                if val == "" or val == "0":
                    meaning = "stable"
                elif val == "-":
                    meaning = "degradation"
                elif val == "+":
                    meaning = "improvement"
                else:
                    log('unrecognized value "{}" when reading transition matrix JSON'.format(val))
                    raise ValueError('unrecognized value "{}" when reading transition matrix JSON'.format(val))
                transitions.append(LCTransitionMeaningDeg(self.nesting.parent.key[row],
                                                  self.nesting.parent.key[col],
                                                  meaning))
        return LCTransitionDefinitionDeg(self.nesting.parent,
                             transitions)

class LandCoverSetupLocalExecutionWidget(
    QtWidgets.QWidget,
    WidgetLandCoverSetupLocalExecutionUi
):
    initial_year_layer_cb: data_io.WidgetDataIOSelectTELayerImport
    target_year_layer_cb: data_io.WidgetDataIOSelectTELayerImport

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.initial_year_layer_cb.populate()
        self.target_year_layer_cb.populate()

    def get_initial_year(self):
        usable_band_info = self.initial_year_layer_cb.get_usable_band_info()
        return usable_band_info.band_info.metadata["year"]

    def get_final_year(self):
        usable_band_info = self.target_year_layer_cb.get_usable_band_info()
        return usable_band_info.band_info.metadata["year"]


class LandCoverSetupRemoteExecutionWidget(
    QtWidgets.QWidget,
    WidgetLandCoverSetupRemoteExecutionUi
):
    initial_year_la: QtWidgets.QLabel
    initial_year_de: QtWidgets.QDateEdit
    target_year_de: QtWidgets.QDateEdit
    target_year_la: QtWidgets.QLabel
    aggregation_method_pb: QtWidgets.QPushButton
    aggregation_dialog: QtWidgets.QDialog

    def __init__(
            self,
            parent=None,
            hide_min_year: typing.Optional[bool] = False,
            hide_max_year: typing.Optional[bool] = False,
            selected_min_year: typing.Optional[int] = 2001,
            selected_max_year: typing.Optional[int] = 2015,
    ):
        super().__init__(parent)
        self.setupUi(self)
        esa_cci_lc_conf = conf.REMOTE_DATASETS["Land cover"]["ESA CCI"]
        min_year = QtCore.QDate(esa_cci_lc_conf["Start year"], 1, 1)
        max_year = QtCore.QDate(esa_cci_lc_conf["End year"], 12, 31)
        self.initial_year_de.setMinimumDate(min_year)
        self.initial_year_de.setMaximumDate(max_year)
        self.target_year_de.setMinimumDate(min_year)
        self.target_year_de.setMaximumDate(max_year)
        self.initial_year_de.setDate(QtCore.QDate(selected_min_year, 1, 1))
        self.target_year_de.setDate(QtCore.QDate(selected_max_year, 12, 31))
        if hide_min_year:
            self.initial_year_la.hide()
            self.initial_year_de.hide()
        if hide_max_year:
            self.target_year_la.hide()
            self.target_year_de.hide()
        self.aggregation_method_pb.clicked.connect(self.open_aggregation_method_dialog)
        self.aggregation_dialog = DlgCalculateLCSetAggregation(
            nesting=get_lc_nesting(),
            parent=self
        )

    def open_aggregation_method_dialog(self):
        self.aggregation_dialog.exec_()
