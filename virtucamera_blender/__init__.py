# VirtuCameraBlender
# Copyright (c) 2021 Pablo Javier Garcia Gonzalez.
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDERS BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY
# OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THE SOFTWARE,
# EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

bl_info = {
    "name" : "VirtuCamera",
    "author" : "Pablo Garcia",
    "description" : "Live camera motion capture",
    "blender" : (2, 80, 0),
    "version" : (1, 0, 1),
    "location" : "3D View > VirtuCamera",
    "warning" : "",
    "category" : "3D View"
}

import bpy
from .virtucamera_blender import *

classes = (
    VirtuCameraState,
    VIEW3D_OT_virtucamera_start,
    VIEW3D_OT_virtucamera_stop,
    VIEW3D_OT_virtucamera_redraw,
    GRAPH_OT_virtucamera_euler_filter,
    VIEW3D_PT_virtucamera_main,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.virtucamera = bpy.props.PointerProperty(type=VirtuCameraState)

def unregister():
    bpy.context.scene.virtucamera.server.stop_serving()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.virtucamera