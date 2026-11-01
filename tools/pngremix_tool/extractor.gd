extends SceneTree

# Headless extractor for PNGTuber Remix models
func _init():
    var args = OS.get_cmdline_user_args()
    if args.size() < 2:
        print("[GDScript Extractor] Missing required arguments!")
        print("Expected: -- <input_file> <output_dir> [model_id] [display_name]")
        quit(1)

    var in_path = args[0]
    var out_dir = args[1].replace("\\", "/")
    if not out_dir.ends_with("/"):
        out_dir += "/"

    var model_id = args[2] if args.size() > 2 else "remix_model"
    var display_name = args[3] if args.size() > 3 else model_id

    var tex_dir = out_dir + "textures/"
    DirAccess.make_dir_recursive_absolute(tex_dir)

    var file = FileAccess.open(in_path, FileAccess.READ)
    if not file:
        print("[GDScript Extractor] Failed to open: ", in_path)
        quit(1)

    var data = file.get_var(true)
    file.close()

    if typeof(data) != TYPE_DICTIONARY or not data.has("sprites_array"):
        print("[GDScript Extractor] Invalid .pngRemix file structure!")
        quit(1)

    var arr = data["sprites_array"]
    print("[GDScript Extractor] Processing " + str(arr.size()) + " sprite definitions...")

    # Canonical layer name mappings
    var mappings = {
        "帽子后": "hat_back.png",
        "_0033_带子": "hat_strap.png",
        "_0034_蝴蝶结": "hat_ribbon.png",
        "_0008_后发": "hair_back.png",
        "_0009_R辫子": "braid_right.png",
        "_0010_L辫子": "braid_left.png",
        "_0002_R足": "foot_right.png",
        "_0003_L足": "foot_left.png",
        "_0004_身": "body_base.png",
        "_0007_领结": "necktie.png",
        "_0000_R手放下": "hand_right_down.png",
        "_0001_L手放下": "hand_left_down.png",
        "_0032_放大镜抬手": "hand_magnifier.png",
        "Duplicate思考手": "hand_thinking.png",
        "_0028_R抬手": "hand_wave_right.png",
        "_0029_L抬手": "hand_wave_left.png",
        "拿苹果": "hand_apple.png",
        "_0011_脸": "face_base.png",
        "_0027_腮红": "blush.png",
        "_0022_A瞳仁": "eye_iris.png",
        "_0023_A瞳孔": "eye_pupil.png",
        "_0024_A眼眶": "eye_contour.png",
        "_0025_A高光": "eye_highlight.png",
        "_0026_A闭眼": "eye_closed.png",
        "_0021_A0": "mouth_closed.png",
        "_0019_A2": "mouth_open.png",
        "_0012_前发": "hair_front.png",
        "_0013_刘海": "hair_bangs.png",
        "_0014_呆毛": "hair_ahoge.png",
        "_0016_发卡": "hairpin.png",
        "帽子": "hat.png",
        "_0035_鸽子": "bird.png",
        "_0037_花花1": "flower_1.png",
        "_0038_花花": "flower_2.png",
        "哭哭泪水": "prop_tears.png",
        "汗": "prop_sweat.png",
        "亮灯": "prop_lightbulb.png",
        "眼镜": "prop_glasses.png"
    }

    var saved_layers = []
    var exported_count = 0

    for s in arr:
        var name = s.get("sprite_name", "")
        if mappings.has(name):
            var filename = mappings[name]
            var b = s.get("img")
            if b and b.size() > 500:
                var out_file = tex_dir + filename
                var f = FileAccess.open(out_file, FileAccess.WRITE)
                if f:
                    f.store_buffer(b)
                    f.close()
                    exported_count += 1
                    var st = s.get("states", [{}])[0]
                    saved_layers.append({
                        "id": filename.replace(".png", ""),
                        "file": filename,
                        "name": name,
                        "z_index": st.get("z_index", 0.0),
                        "position": [st.get("position", Vector2.ZERO).x, st.get("position", Vector2.ZERO).y],
                        "offset": [st.get("offset", Vector2.ZERO).x, st.get("offset", Vector2.ZERO).y]
                    })
                    print("  [+] " + filename + " <- " + name)

    # Build manifest
    var manifest = {
        "id": model_id,
        "name": model_id,
        "display_name": display_name,
        "version": "1.0.0",
        "canvas": {
            "width": 900,
            "height": 900,
            "display_width": 280,
            "display_height": 380,
            "origin_x": 450,
            "origin_y": 450
        },
        "pivots": {
            "head": [450, 480],
            "hair_back": [450, 240],
            "braid_left": [255, 460],
            "braid_right": [645, 460],
            "ahoge": [432, 200],
            "bird": [630, 180],
            "body": [450, 590]
        },
        "eye_tracking": {
            "max_offset_x": 10,
            "max_offset_y": 7,
            "iris_layer": "eye_iris",
            "pupil_layer": "eye_pupil",
            "highlight_layer": "eye_highlight"
        },
        "layers": saved_layers
    }

    var mf = FileAccess.open(out_dir + "model.json", FileAccess.WRITE)
    if mf:
        mf.store_string(JSON.stringify(manifest, "  "))
        mf.close()
        print("[GDScript Extractor] Extracted " + str(exported_count) + " diffuse textures.")
        print("[GDScript Extractor] Model configuration saved to: " + out_dir + "model.json")

    quit(0)
