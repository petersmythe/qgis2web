# -*- coding: utf-8 -*-
"""
/***************************************************************************
 qgis2web
 (c) Tom Chadwin

 leafletWriter.py original by:
                             -------------------
        begin                : 2014-04-29
        copyright            : (C) 2013 by Riccardo Klinger
        email                : riccardo.klinger@geolicious.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 3 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

from qgis.core import (QgsApplication,
                       QgsProject,
                       QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform,
                       QgsMapLayer,
                       QgsMessageLog)
import codecs
import shutil
import traceback
from PyQt5.QtCore import (Qt,
                          QObject)
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import QApplication
import os
from datetime import datetime
import re
from qgis2web.mapboxFileScripts import (writeFoldersAndFiles,
                                        writeCSS,
                                        writeHTMLstart)
from qgis2web.mapboxLayerScripts import writeVectorLayer
from qgis2web.mapboxScriptStrings import (jsonScript,
                                          scaleDependentLabelScript,
                                          mapScript,
                                          featureGroupsScript,
                                          extentScript,
                                          rasterScript,
                                          wmsScript,
                                          scaleDependentLayerScript,
                                          addressSearchScript,
                                          endHTMLscript,
                                          addLayersList,
                                          highlightScript,
                                          crsScript,
                                          scaleBar,
                                          scaleDependentScript,
                                          titleSubScript,
                                          getVTStyles,
                                          getVTLabels)
from qgis2web.utils import (ALL_ATTRIBUTES, PLACEMENT, exportVector,
                            exportRaster, safeName, scaleToZoom)
from qgis2web.writer import (Writer,
                             WriterResult,
                             translator)
from qgis2web.feedbackDialog import Feedback
from qgis2web.bridgestyle.qgis import layerStyleAsMapbox


class MapboxWriter(Writer):
    """
    Writer for creation of web maps based on the Leaflet
    JavaScript library.
    """

    def __init__(self):
        super(MapboxWriter, self).__init__()

    @classmethod
    def type(cls):
        return 'mapbox'

    @classmethod
    def name(cls):
        return QObject.tr(translator, 'Mapbox')

    def write(self, iface, dest_folder, feedback=None):
        if not feedback:
            feedback = Feedback()

        feedback.showFeedback('Creating Mapbox map...')
        self.preview_file = self.writeMapbox(
            iface,
            feedback,
            layer_list=self.layers,
            popup=self.popup,
            visible=self.visible,
            json=self.json,
            cluster=self.cluster,
            getFeatureInfo=self.getFeatureInfo,
            params=self.params,
            folder=dest_folder)
        result = WriterResult()
        result.index_file = self.preview_file
        result.folder = os.path.dirname(self.preview_file)
        for dirpath, dirnames, filenames in os.walk(result.folder):
            result.files.extend([os.path.join(dirpath, f) for f in filenames])
        return result

    @classmethod
    def writeMapbox(
            cls, iface, feedback, folder,
            layer_list, visible, cluster,
            json, getFeatureInfo, params, popup):
        outputProjectFileName = folder
        QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
        legends = {}
        mapUnitLayers = []
        canvas = iface.mapCanvas()
        project = QgsProject.instance()
        mapSettings = canvas.mapSettings()
        title = project.title()
        pluginDir = os.path.dirname(os.path.realpath(__file__))
        stamp = datetime.now().strftime("%Y_%m_%d-%H_%M_%S_%f")
        outputProjectFileName = os.path.join(outputProjectFileName,
                                             'qgis2web_' + unicode(stamp))
        outputIndex = os.path.join(outputProjectFileName, 'index.html')

        minify = params["Data export"]["Minify GeoJSON files"]
        precision = params["Data export"]["Precision"]
        extent = params["Scale/Zoom"]["Extent"]
        minZoom = params["Scale/Zoom"]["Min zoom level"]
        maxZoom = params["Scale/Zoom"]["Max zoom level"]
        restrictToExtent = params["Scale/Zoom"]["Restrict to extent"]
        matchCRS = params["Appearance"]["Match project CRS"]
        addressSearch = params["Appearance"]["Add address search"]
        locate = params["Appearance"]["Geolocate user"]
        measure = params["Appearance"]["Measure tool"]
        highlight = params["Appearance"]["Highlight on hover"]
        layerSearch = params["Appearance"]["Layer search"]
        popupsOnHover = params["Appearance"]["Show popups on hover"]
        template = params["Appearance"]["Template"]
        widgetAccent = params["Appearance"]["Widget Icon"]
        widgetBackground = params["Appearance"]["Widget Background"]

        usedFields = [ALL_ATTRIBUTES] * len(popup)

        QgsApplication.initQgis()

        crsSrc = mapSettings.destinationCrs()
        crs = QgsCoordinateReferenceSystem.EpsgCrsId
        crsDest = QgsCoordinateReferenceSystem(4326, crs)
        xform = QgsCoordinateTransform(crsSrc, crsDest, project)

        dataStore, cssStore = writeFoldersAndFiles(pluginDir, feedback,
                                                   outputProjectFileName,
                                                   cluster, measure,
                                                   matchCRS, layerSearch,
                                                   canvas, addressSearch,
                                                   locate)
        writeCSS(cssStore, mapSettings.backgroundColor().name(), feedback,
                 widgetAccent, widgetBackground)

        wfsLayers = ""
        labelCode = ""
        vtLabels = {}
        vtStyles = {}
        useMultiStyle = False
        useHeat = False
        useVT = False
        useShapes = False
        useOSMB = False
        useWMS = False
        useWMTS = False
        useRaster = False
        vtSources = []
        vtLayers = ["""
        {
            "id": "background",
            "type": "background",
            "layout": {},
            "paint": {
                "background-color": "#ffffff"
            }
        }"""]
        vLayers = []
        rLayers = []
        scaleDependentLayers = ""
        labelVisibility = ""
        new_src = ""
        jsons = ""
        sources = []
        crs = QgsCoordinateReferenceSystem.EpsgCrsId
        exp_crs = QgsCoordinateReferenceSystem(4326, crs)
        lyrCount = 0
        for layer, jsonEncode, eachPopup, clst in zip(layer_list, json,
                                                      popup, cluster):
            rawLayerName = layer.name()
            safeLayerName = safeName(rawLayerName) + "_" + unicode(lyrCount)
            vts = layer.customProperty("VectorTilesReader/vector_tile_url")
            if layer.providerType() != 'WFS' or jsonEncode is True:
                if layer.type() == QgsMapLayer.VectorLayer and vts is None:
                    feedback.showFeedback('Exporting %s to JSON...' %
                                          layer.name())
                    exportVector(layer, safeLayerName, dataStore,
                                 restrictToExtent, iface, extent, precision,
                                 exp_crs, minify)
                    jsons += jsonScript(safeLayerName)
                    sources.append("""
        "%s": {
            "type": "geojson",
            "data": json_%s
        }
                    """ % (safeLayerName, safeLayerName))
                    scaleDependentLabels = \
                        scaleDependentLabelScript(layer, safeLayerName)
                    labelVisibility += scaleDependentLabels
                    feedback.completeStep()

                elif layer.type() == QgsMapLayer.RasterLayer:
                    if layer.dataProvider().name() != "wms":
                        layersFolder = os.path.join(outputProjectFileName,
                                                    "data")
                        exportRaster(layer, lyrCount, layersFolder,
                                     feedback, iface, matchCRS)
                        rasterPath = './data/' + safeLayerName + '.png'
                        extent = layer.extent()
                        bbox = xform.transformBoundingBox(extent)
                        print(bbox.xMinimum())
                        sources.append("""
        "%s": {
            "type": "image",
            "url": "%s",
            "coordinates": [
                [%f, %f],
                [%f, %f],
                [%f, %f],
                [%f, %f]
            ]
        }""" % (safeLayerName, rasterPath,
                bbox.xMinimum(), bbox.yMinimum(),
                bbox.xMaximum(), bbox.yMinimum(),
                bbox.xMaximum(), bbox.yMaximum(),
                bbox.xMinimum(), bbox.yMaximum()))
            lyrCount += 1

        for count, layer in enumerate(layer_list):
            rawLayerName = layer.name()
            safeLayerName = safeName(rawLayerName) + "_" + unicode(count)
            if layer.type() == QgsMapLayer.VectorLayer:
                (new_src,
                 legends,
                 wfsLayers,
                 labelCode,
                 vtLabels,
                 vtStyles,
                 useMapUnits,
                 useMultiStyle,
                 useHeat,
                 useVT,
                 useShapes,
                 useOSMB,
                 vtSources,
                 vtLayers,
                 vLayers) = writeVectorLayer(layer, safeLayerName,
                                              usedFields[count], highlight,
                                              popupsOnHover, popup[count],
                                              outputProjectFileName,
                                              wfsLayers, cluster[count],
                                              visible[count], json[count],
                                              legends, new_src, canvas, count,
                                              restrictToExtent, extent,
                                              feedback, labelCode, vtLabels,
                                              vtStyles, useMultiStyle,
                                              useHeat, useVT, useShapes,
                                              useOSMB, vtSources, vtLayers,
                                              vLayers)
            elif layer.type() == QgsMapLayer.RasterLayer:
                if layer.dataProvider().name() == "wms":
                    feedback.showFeedback('Writing %s as WMS layer...' %
                                          layer.name())
                    new_obj, useWMS, useWMTS = wmsScript(layer, safeLayerName,
                                                         useWMS, useWMTS,
                                                         getFeatureInfo[count])
                    feedback.completeStep()
                else:
                    feedback.showFeedback('Writing %s as raster layer...' %
                                          layer.name())
                    rLayers.append(rasterScript(layer, safeLayerName))
                    feedback.completeStep()
        sprite = ("https://s3-eu-west-1.amazonaws.com/tiles.os.uk/styles/"
                  "open-zoomstack-outdoor/sprites")
        glyphs = ("https://glfonts.lukasmartinelli.ch/fonts/{fontstack}/"
                  "{range}.pbf")
        s = """
var styleJSON = {
    "version": 8,
    "name": "qgis2web export",
    "pitch": 0,
    "light": {
        "intensity": 0.2
    },
    "sources": {%s},
    "sprite": "%s",
    "glyphs": "%s",
    "layers": [%s],
}""" % (",".join(vtSources + sources), sprite, glyphs,
        ",".join(vtLayers + vLayers + rLayers))
        mbStore = os.path.join(outputProjectFileName, 'mapbox')
        if not os.path.exists(mbStore):
            shutil.copytree(os.path.join(os.path.dirname(__file__),
                                         "mapbox"),
                            mbStore)
        with codecs.open(os.path.join(mbStore, "style.js"),
                         'w', encoding='utf-8') as f:
            f.write(unicode(s))
            f.close()
        pt0 = canvas.center()
        pt1 = xform.transform(pt0)
        center = '[' + str(pt1.x()) + ','
        center += str(pt1.y()) + ']'
        center.replace('nan', '0')
        bearing = 360 - canvas.rotation()
        zoom = scaleToZoom(canvas.scale())
        new_src = jsons + """
<script src="./mapbox/style.js"></script>
<script>
var map = new mapboxgl.Map({
 container: 'map',
 style: styleJSON,
 center: %s,
 zoom: %s,
 bearing: %s
});
map.addControl(new mapboxgl.NavigationControl());
</script>""" % (center, zoom, bearing)
        # try:
        writeHTMLstart(outputIndex, title, cluster, addressSearch,
                       measure, matchCRS, layerSearch, canvas,
                       locate, new_src, template, feedback,
                       useMultiStyle, useHeat, useShapes,
                       useOSMB, useWMS, useWMTS, useVT)
        # except Exception as e:
        #     QgsMessageLog.logMessage(traceback.format_exc(), "qgis2web",
        #                              level=QgsMessageLog.CRITICAL)
        #     QApplication.restoreOverrideCursor()
        # finally:
        #     QApplication.restoreOverrideCursor()
        return outputIndex
