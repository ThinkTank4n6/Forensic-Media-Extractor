# Bundled image models

Shipped since v2026.11.317, copied unchanged from GLEAPP 2026.5.7
(`gleapp/models/`, MIT, Copyright (c) 2026 charpy4n6, Alexis Brignoni),
which took each from its publisher. Each keeps its own licence, beside it.
`fme/engines/models.py` checks each file's SHA-256 before it is used; one
that does not match is not used, and the search says so.

| file | what it is | licence | SHA-256 |
| --- | --- | --- | --- |
| `dinov2_small.onnx` | DINOv2-small (Meta AI), content search | Apache-2.0, `LICENSE-dinov2` | `b88d3157590250f1ed413bd41289ce2b4f6c65f3699684eaa913efc2330e2822` |
| `face_detection_yunet_2023mar.onnx` | YuNet 2023mar (Shiqi Yu), finding faces | MIT, `LICENSE-yunet` | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` |
| `face_recognition_sface_2021dec.onnx` | SFace 2021dec (OpenCV Zoo), describing a face | Apache-2.0, `LICENSE-sface` | `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79` |

**Everything these models produce is an investigative lead only.** A score
says how alike a model finds two pictures or two faces. It is not an
identification of anyone or anything, it applies no category, and SFace was
trained on adults' faces: children's faces, the case it is most used on,
are the case it is least measured on. DINOv2 loads on OpenCV 5.0 or newer.

A path set in Settings is used instead of the bundled model.
