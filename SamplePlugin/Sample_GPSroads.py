from UCwinRoadCOM import *
from LoggerProxy import LoggerProxy
from UCwinRoadUtils import *
from CallbackHandlers import *
import time
import win32com.client as com
import pandas as pd
from UCwinRoadOpenGL import *
from OpenGL.GLUT import *
from OpenGL.GL import *
from math import sqrt

#map data
wholeRoadMap = []
#size in UCwinRoad by metre
mapHeight = 1500
rotationAngle = 180
GLX = 1
GLY = 1

firstTime = True
eventList = []

class OpenGLDrawMap:
    def __init__(self):
        pass
    
    def DrawCar(self, carX, carY, halfSize, flipX):
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glDisable(GL_LIGHTING)
        
        #red
        glColor4f(1.0, 0.0, 0.0, 1.0)
        
        glBegin(GL_QUADS)
        #need to use halfSize in order to see the car
        Left = flipX -  carX - halfSize
        Bottom = carY - halfSize
        Right = flipX -  carX + halfSize
        Top = carY + halfSize
        glVertex2f(Left, Bottom)
        glVertex2f(Right, Bottom)
        glVertex2f(Right, Top)
        glVertex2f(Left, Top)
        glEnd()
    
    def DrawRoad(self, wholeRoadMap, halfWidth, flipX):
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_COLOR_MATERIAL)

        for lane in wholeRoadMap:
            glBegin(GL_QUAD_STRIP)
            #dark green
            glColor4f(0.0, 0.3, 0.0, 1.0)
            #this is a sizing for loop in order to see the map
            for i in range(1, len(lane)-1):
                pt1 = lane[i]
                pt2 = lane[i-1]
                dx = pt2[0] - pt1[0]
                dy = pt2[1] - pt1[1]
                roadLen = sqrt(dx*dx+dy*dy)
                if roadLen == 0.0:
                    continue
                dx /= roadLen
                dy /= roadLen
                px = dy
                py = dx
                glVertex2f(flipX - pt1[0] + px*halfWidth, pt1[1] + py*halfWidth)
                glVertex2f(flipX - pt1[0] - px*halfWidth, pt1[1] - py*halfWidth)
                
            glEnd()

class MainFormHandler(HandlerBase):
    #set keyboard event
    def OnKeyDown(self, key, Shift):
        logProxy.logger.info("Key={}".format(key))
        global deltaX, deltaY, mapHeight
        match key:
            #'Insert' key zoom in
            case 45:
                if (mapHeight > 500) :
                    deltaX += 250
                    deltaY += 250
                    mapHeight -= 500
            #'PageUp' key zoom out
            case 33:
                deltaX -= 250
                deltaY -= 250
                mapHeight += 500
            #'PageDown' key rihgt
            case 34:
                deltaX -= 100
            #'End' key down
            case 35:
                deltaY -= 100
            #'Delete' key left
            case 46:
                deltaX += 100
            #'Home' key up
            case 36:
                deltaY += 100  

class MainOpenGLHandler(HandlerBase):
    def OnOpenGLAfterPaint(self):
        #get the window size
        GLsize = winRoadProxy.MainForm.MainOpenGL.Size
        global GLX, GLY
        #by pixel
        GLX = GLsize.X
        GLY = GLsize.Y

class TransientInstanceHandler(HandlerBase):
    def OnAfterCalculateMovement(self, dTimeInSeconds, proxy):
        proxyCar = com.Dispatch(proxy)
        if proxyCar.TransientType == const._TransientCar:
            if saveCarID == proxyCar.ID :
                #get the car position by metre
                global carX, carY
                carX = proxyCar.RearPosition.X
                carY = proxyCar.RearPosition.Z
                #set car in the map center at first time
                global mapX, mapY
                global firstTime
                if firstTime:
                    mapX = carX
                    mapY = carY
                    firstTime = False

class VirtualDisplayEventHandler2D(HandlerBase):
    #set 2D virtual display event
    def OnDirectDraw(self):
        #draw background
        OpenGLSample.DrawBack(1.0, 1.0, 1.0, 0.5)
        
        #get virtual display size
        global eventList
        for targetEvent in eventList:
            if self == targetEvent[1]:
                if targetEvent[0].Placement == const._ptPixel:
                    #by pixel
                    VDwidth = targetEvent[0].PixelBounds.Right
                    VDheight = targetEvent[0].PixelBounds.Bottom
                if targetEvent[0].Placement == const._ptPercent:
                    #exchange percent to pixel, make the map in a same rate
                    global GLX, GLY
                    VDwidth = targetEvent[0].PercentBounds.Right * GLX * 0.01
                    VDheight = targetEvent[0].PercentBounds.Bottom * GLY * 0.01
        
        global deltaX, deltaY, mapHeight
        global mapX, mapY        
        #map size
        if VDheight == 0:
            VDheight = 1
        mapWidth = mapHeight / VDheight * VDwidth
        #by metre
        left = mapX - mapWidth * 0.5 + deltaX
        right = left + mapWidth + 1
        bottom = mapY - mapHeight * 0.5 + deltaY
        top = bottom + mapHeight + 1
        #set the map to OpenGL
        OpenGLSample.SetOrthoView(rotationAngle, left, right, bottom, top)
        
        flipX = left+right
        #flipY = top+bottom
        
        global carX, carY
        #the car is up the road so draw the car first in OpenGL
        halfSize = 15.0
        #for rotationAngle = 180
        drawMap.DrawCar(carX, carY, halfSize, flipX)
        
        global wholeRoadMap
        halfWidth = 10.5  
        drawMap.DrawRoad(wholeRoadMap, halfWidth, flipX)

class VirtualDisplayEventHandler3D(HandlerBase):
    #set 3D virtual display event
    def OnDirectDraw(self):
        #draw background
        OpenGLSample.DrawBack(1.0, 1.0, 1.0, 0.5)
        
        #get virtual display size
        for targetEvent in eventList:
            if self == targetEvent[1]:
                #by pixel
                VDwidth = targetEvent[0].Width
                VDheight = targetEvent[0].Height
        
        global deltaX, deltaY, mapHeight
        global mapX, mapY        
        #map size
        if VDheight == 0:
            VDheight = 1
        mapWidth = mapHeight / VDheight * VDwidth
        #by metre
        left = mapX - mapWidth * 0.5 + deltaX
        right = left + mapWidth + 1
        bottom = mapY - mapHeight * 0.5 + deltaY
        top = bottom + mapHeight + 1
        #set the map to OpenGL
        OpenGLSample.SetOrthoView(rotationAngle, left, right, bottom, top)
        
        flipX = left+right
        #flipY = top+bottom
        
        global carX, carY
        #the car is up the road so draw the car first in OpenGL
        halfSize = 15.0
        #for rotationAngle = 180
        drawMap.DrawCar(carX, carY, halfSize, flipX)
        
        global wholeRoadMap
        halfWidth = 10.5  
        drawMap.DrawRoad(wholeRoadMap, halfWidth, flipX)

class RibbonButtonStartHandler(RibbonButtonHandler):
    #set start button event
    def OnClick(self):
        logProxy.logger.info('Start DirectDraw')        
        
        #get the whole map data
        global wholeRoadMap
        wholeRoadMap.clear() 
        #this function returns the project currently opened by the application
        proxyProj = winRoadProxy.Project
        #going through all roads in program
        roadCount = proxyProj.RoadsCount        
        for i in range(roadCount):
            road = proxyProj.Road(i)
            if road is not None:
                laneCount = road.RoadLanesCount
                for j in range(laneCount):
                    lane = road.RoadLane(j)
                    if lane is not None:
                        #all the roads
                        lanePoints = []
                        length = int(lane.Length)                        
                        for s in range(0, length, 5):
                            pos = lane.GetPosition(s, const._ldRoad)
                            lanePoints.append([pos.X, pos.Z])
                        wholeRoadMap.append(lanePoints)

        global eventList
        vdList = winRoadProxy.VirtualDisplaysPlugin.GetVirtualDisplays()
        vdCount = vdList.Count
        # Set callback to VirtualDisplay
        for i in range(vdCount):
            VD = vdList.Items(i)
            logProxy.logger.info("VirtualDisplay Name={}".format(VD.Name))
            if VD.VirtualDisplayType == const._vd2DOverlay:
                SetCallbackHandlers(eventList, VD, VirtualDisplayEventHandler2D)
            elif VD.VirtualDisplayType == const._vd3DObject:
                SetCallbackHandlers(eventList, VD, VirtualDisplayEventHandler3D)

        # Set callback to CarInstance
        global saveCarID
        traffic = winRoadProxy.ApplicationServices.SimulationCore.TrafficSimulation
        driver = traffic.Driver
        if driver is not None:
            car = driver.CurrentCar
            if car is not None:
                logProxy.logger.info("car.ID={}".format(car.ID))
                saveCarID = car.ID
                SetCallbackHandlers(eventList, car, TransientInstanceHandler)
        else :
            logProxy.logger.info("no driver")
            
        # Set callback to MainForm
        mainForm = winRoadProxy.MainForm
        SetCallbackHandlers(eventList, mainForm, MainFormHandler)
        mainOpenGL = mainForm.MainOpenGL
        SetCallbackHandlers(eventList, mainOpenGL, MainOpenGLHandler)
        
        # reset the firsttime
        global firstTime
        global deltaX, deltaY
        firstTime = True
        deltaX = 0
        deltaY = 0

class RibbonButtonStopHandler(RibbonButtonHandler):
    #set stop button event
    def OnClick(self):
        logProxy.logger.info('Stop DirectDraw')
        global eventList
        CloseCallbackEvent(eventList)

class RibbonUI:
    def __init__(self) -> None:
        self.EventList = []

    #create the manu
    def MakeRibbonTab(self, Parent, partsName, caption):
        if Parent is not None:
            tab = Parent.GetTabByName(partsName)
            if tab is None:
                tab = Parent.CreateTab(partsName, 10000)
                tab.Caption = caption
            return tab

    def MakeRibbonGroup(self, Parent, partsName, caption):
        if Parent is not None:
            group = Parent.GetGroupByName(partsName)
            if group is None:
                group = Parent.CreateGroup(partsName, 100)
                group.Caption = caption
            return group
    
    def MakeRibbonPanel(self, Parent, partsName, caption):
        if Parent is not None:
            panel = Parent.GetControlByName(partsName)
            if panel is None:
                panel = Parent.CreatePanel(partsName)    
            return panel
    #set the callback
    def SetCallbackEvent(self, button, handler):
        if button is not None:
            isValue = button.IsSetCallbackOnClick()
        if isValue == False :
            Event = com.WithEvents(button, handler)
            Event.SetCOMEventClass(Event)
            button.RegisterEventHandlers()
            self.EventList.append(Event)
            return Event
    #create the button
    def MakeRibbonButton(self, Parent, partsName, caption, handler):
        if Parent is not None:
            button = Parent.GetControlByName(partsName)
            if button is None:
                button = Parent.CreateButton(partsName)
                button.Caption = caption
                self.SetCallbackEvent(button, handler)
            return button

    def DeleteControlFromParent(self, child, Parent):
        if child is not None:
            child.UnRegisterEventHandlers()
            if Parent is not None:
                Parent.DeleteControl(child)
            child = None

    def CloseCallbackEvent(self):
        if self.EventList is not None:
            for Event in self.EventList:
                Event.close()
            self.EventList.clear()

    def MakeRibbonUI(self):
        mainForm = winRoadProxy.MainForm
        #menu
        self.ribbonMenu = mainForm.MainRibbonMenu
        #tab
        self.ribbonTab = self.MakeRibbonTab(self.ribbonMenu, 'PythonAPISamples', 'Python_API_Samples')
        #group
        self.ribbonGroup = self.MakeRibbonGroup(self.ribbonTab, 'Sample_GPSroads', 'Sample_GPSroads')
        #button
        self.ribbonButtonStart = self.MakeRibbonButton(self.ribbonGroup, 'ButtonStart', 'Start', RibbonButtonStartHandler)
        self.ribbonButtonStop = self.MakeRibbonButton(self.ribbonGroup, 'ButtonStop', 'Stop', RibbonButtonStopHandler)
        self.ribbonButtonStart.Width = 150
        self.ribbonButtonStop.Width = 150

    def KillRibbonUI(self):
        self.DeleteControlFromParent(self.ribbonButtonStart, self.ribbonGroup)
        self.DeleteControlFromParent(self.ribbonButtonStop, self.ribbonGroup)
        self.ribbonTab.DeleteGroup(self.ribbonGroup)
        self.ribbonGroup = None
        if self.ribbonTab.RibbonGroupsCount == 0 :
            self.ribbonMenu.DeleteTab(self.ribbonTab)
        self.ribbonTab = None
        self.ribbonMenu = None
        self.CloseCallbackEvent()

def main():
    try:
        start = time.perf_counter_ns()
        global winRoadProxy
        winRoadProxy = UCwinRoadComProxy()
        global const
        const = winRoadProxy.const
        
        #create log file
        scriptName = 'Sample_GPSroads'
        global logProxy
        logfilepath = winRoadProxy.PythonPluginDirectory() + scriptName + '.log'
        logProxy = LoggerProxy(scriptName, logfilepath)
        logProxy.logger.info('Start '+ scriptName)
        
        #create menu class
        ribbon = RibbonUI()
        ribbon.MakeRibbonUI()
        
        #create OpenGL class
        global OpenGLSample
        OpenGLSample = OpenGLSamples()
        global drawMap
        drawMap = OpenGLDrawMap()

        #event loop
        loopFlg = True
        winRoadProxy.ApplicationServices.IsPythonScriptRun = loopFlg
        while loopFlg:
            #pythoncom.PumpWaitingMessages()
            time.sleep(0.005)
            loopFlg = winRoadProxy.ApplicationServices.IsPythonScriptRun
            if loopFlg == False:
                logProxy.logger.info("loopFlg={}".format(loopFlg))
                logProxy.logger.info("Script close")
        winRoadProxy.ApplicationServices.IsPythonScriptRun = loopFlg

    finally:
        elapsed_time = time.perf_counter_ns() - start
        logProxy.logger.info("Total:{}ms".format(elapsed_time/1000000))
        logProxy.logger.info('End '+ scriptName)
        CloseCallbackEvent(eventList)
        ribbon.KillRibbonUI()
        logProxy.killLogger()
        del winRoadProxy

if __name__ == '__main__':
    main()
