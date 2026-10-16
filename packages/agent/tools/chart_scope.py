import base64
import hashlib
import math
import re

def comparison_filter_label(value):
    if not value or value == "all":
        return "全部学校"
    return {"rural": "乡村", "urban": "城镇"}.get(value, str(value))

def dimension_compare_scope(result):
    value = result.get("overview") or result
    if value.get("object_type") != "region_compare" or not value.get("target_dimension"):
        return None
    if value.get("target_stat") == "count" or value.get("output_focus") == "school_count":
        return None
    years = value.get("school_years") or []
    year = value.get("year") or (years[0] if len(years) == 1 else None)
    if year is None:
        matched = re.fullmatch(r"(\d{4})(?:年)?", str(value.get("year_desc") or "").strip())
        year = matched.group(1) if matched else None
    answer_scope = result.get("answer_scope") or value.get("answer_scope") or {}
    requested = set(answer_scope.get("requested_result_statistics") or ())
    counts = [value.get("school_count_a"), value.get("school_count_b")]

    count_required = bool(
        requested & {"school_count", "school_count_a", "school_count_b"}
        or any(count is not None for count in counts)
    )
    return {
        "dimension": value.get("target_dimension"), "year": str(year or ""),
        "filter": value.get("school_filter") or "all",
        "regions": [value.get("region_name_a"), value.get("region_name_b")],
        "values": [value.get("total_score_a"), value.get("total_score_b")],
        "counts": counts, "count_required": count_required,
        "diff": value.get("diff"),
    }

def capture_figure_scene(fig):
    pass
    from matplotlib.container import BarContainer
    fig.canvas.draw()
    axes = []
    for ax in fig.axes:
        bars = []
        for container in ax.containers:
            if isinstance(container, BarContainer):
                for bar in container.patches:
                    bars.append({
                        "x": float(bar.get_x() + bar.get_width() / 2),
                        "width": float(bar.get_width()), "height": float(bar.get_height()),
                        "bottom": float(bar.get_y()), "orientation": container.orientation,
                    })
        axes.append({
            "projection": ax.name, "title": ax.get_title(),
            "side_titles": [ax.get_title(loc="left"), ax.get_title(loc="right")],
            "xlabel": ax.get_xlabel(), "ylabel": ax.get_ylabel(),
            "xticks": [t.get_text() for t in ax.get_xticklabels()],
            "xtick_positions": [float(x) for x in ax.get_xticks()],
            "texts": [t.get_text() for t in ax.texts],
            "legend": [t.get_text() for t in ax.get_legend().get_texts()] if ax.get_legend() else [],
            "bars": sorted(bars, key=lambda bar: bar["x"]),
            "lines": len(ax.lines), "collections": len(ax.collections),
            "images": len(ax.images), "patches": len(ax.patches),
            "x_inverted": bool(ax.xaxis_inverted()),
        })
    return {"version": "matplotlib-scene-v1", "texts": [t.get_text() for t in fig.texts], "axes": axes}

def validate_dimension_compare_charts(result, charts):
    expected = dimension_compare_scope(result)
    if expected is None:
        return {"status": "NOT_APPLICABLE", "failures": []}
    failures = []
    if len(charts) != 1:
        failures.append("dimension_compare_requires_one_chart")
    if not expected["year"] or any(v is None for v in expected["regions"] + expected["values"]) or (
        expected["count_required"] and any(count is None for count in expected["counts"])
    ):
        failures.append("dimension_compare_scope_incomplete")
    for chart in charts:
        scene = chart.get("rendered_scene") or {}
        if scene.get("version") != "matplotlib-scene-v1":
            failures.append("rendered_scene_missing")
        image_data = chart.get("base64") or ""
        try:
            digest = hashlib.sha256(base64.b64decode(image_data.split(",")[-1], validate=True)).hexdigest()
            if not image_data or digest != scene.get("png_sha256"):
                failures.append("rendered_scene_image_mismatch")
        except (ValueError, TypeError):
            failures.append("rendered_scene_image_mismatch")
        axes = scene.get("axes") or []
        if len(axes) != 1 or axes[0].get("projection") != "rectilinear":
            failures.append("dimension_compare_extra_axes_or_radar")
        all_text = " ".join(scene.get("texts") or [])
        for ax in axes:
            all_text += " " + " ".join([str(ax.get("title") or ""), str(ax.get("xlabel") or ""), str(ax.get("ylabel") or ""), *(ax.get("side_titles") or []), *(ax.get("texts") or []), *(ax.get("legend") or []), *(ax.get("xticks") or [])])
        required = [expected["dimension"], expected["year"], "分"]
        required.append(comparison_filter_label(expected["filter"]))
        if any(not token or token not in all_text for token in required):
            failures.append("dimension_compare_rendered_scope_missing")
        dimensions = ("数字资源", "教育教学", "数字素养", "基础设施", "教育治理", "保障机制")
        if any(token in all_text for token in ("六大维度", "六维", "综合", "雷达")) or any(dim != expected["dimension"] and dim in all_text for dim in dimensions):
            failures.append("dimension_compare_rendered_scope_widened")
        if len(axes) == 1:
            ax = axes[0]
            if ax.get("xticks") != expected["regions"] or ax.get("x_inverted"):
                failures.append("dimension_compare_region_order_mismatch")
            bars = ax.get("bars") or []
            if [bar.get("x") for bar in bars] != ax.get("xtick_positions"):
                failures.append("dimension_compare_bar_label_positions_mismatch")
            if len(bars) != 2 or ax.get("patches") != 2 or any(ax.get(k) for k in ("lines", "collections", "images")):
                failures.append("dimension_compare_requires_two_bars_only")
            for bar, score in zip(bars, expected["values"]):
                try:
                    valid = bar.get("orientation") == "vertical" and bar.get("bottom") == 0 and math.isfinite(float(bar["height"])) and abs(float(bar["height"]) - float(score)) <= 0.005000001
                except (ValueError, TypeError, KeyError):
                    valid = False
                if not valid:
                    failures.append("dimension_compare_bar_value_mismatch")
            legend = ax.get("legend") or []
            required_legend = (
                [f"{name}（有效学校{count}所）" for name, count in zip(expected["regions"], expected["counts"])]
                if expected["count_required"] else expected["regions"]
            )
            if legend != required_legend:
                failures.append("dimension_compare_denominator_or_legend_order_mismatch")
        try:
            difference = float(expected["values"][0]) - float(expected["values"][1])
            if expected["diff"] is not None and abs(difference - float(expected["diff"])) > 0.010000001:
                failures.append("dimension_compare_difference_value_mismatch")
            shown_difference = float(expected["diff"]) if expected["diff"] is not None else difference
            difference_label = f"{expected['regions'][0]}减{expected['regions'][1]}：{shown_difference:+.2f}分"
            if difference_label not in all_text:
                failures.append("dimension_compare_difference_direction_missing")
        except (TypeError, ValueError):
            failures.append("dimension_compare_difference_value_missing")
    return {"status": "FAIL" if failures else "PASS", "scope": "rendered_region_dimension_compare", "failures": sorted(set(failures))}

def validate_overall_compare_charts(result, charts):
    pass
    value = result.get("overview") or result
    scope = result.get("answer_scope") or value.get("answer_scope") or {}
    if (value.get("object_type") != "region_compare" or value.get("target_dimension")
            or scope.get("granularity") != "overall" or value.get("dimensions_comparison")):
        return {"status": "NOT_APPLICABLE", "failures": []}
    regions = [value.get("region_name_a"), value.get("region_name_b")]
    scores = [value.get("comparison_value_a"), value.get("comparison_value_b")]
    counts = [value.get("school_count_a"), value.get("school_count_b")]
    requested = set(scope.get("requested_result_statistics") or ())
    count_required = bool(requested & {"school_count", "school_count_a", "school_count_b"}
                          or any(count is not None for count in counts))
    failures = []
    if len(charts) != 1:
        failures.append("overall_compare_requires_one_chart")
    if any(item is None for item in regions + scores) or (count_required and any(count is None for count in counts)):
        failures.append("overall_compare_scope_incomplete")
    year = str(value.get("year") or "")
    delta = value.get("diff")
    if not year or delta is None:
        failures.append("overall_compare_difference_or_year_missing")
    for chart in charts:
        scene = chart.get("rendered_scene") or {}
        image_data = chart.get("base64") or ""
        try:
            digest = hashlib.sha256(base64.b64decode(image_data.split(",")[-1], validate=True)).hexdigest()
            if not image_data or scene.get("version") != "matplotlib-scene-v1" or digest != scene.get("png_sha256"):
                failures.append("overall_compare_scene_or_image_mismatch")
        except (ValueError, TypeError):
            failures.append("overall_compare_scene_or_image_mismatch")
        axes = scene.get("axes") or []
        if len(axes) != 1 or axes[0].get("projection") != "rectilinear":
            failures.append("overall_compare_extra_axes_or_radar")
            continue
        ax = axes[0]
        bars = ax.get("bars") or []
        if (len(bars) != 2 or ax.get("patches") != 2 or any(ax.get(k) for k in ("lines", "collections", "images"))
                or ax.get("xticks") != regions or ax.get("x_inverted")):
            failures.append("overall_compare_two_bars_or_region_order_mismatch")
        for bar, expected in zip(bars, scores):
            try:
                if (bar.get("orientation") != "vertical" or bar.get("bottom") != 0
                        or abs(float(bar["height"]) - float(expected)) > 0.005000001):
                    failures.append("overall_compare_bar_value_mismatch")
            except (TypeError, ValueError, KeyError):
                failures.append("overall_compare_bar_value_mismatch")
        expected_legend = ([f"{name}（有效学校{count}所）" for name, count in zip(regions, counts)]
                           if count_required else regions)
        if ax.get("legend") != expected_legend:
            failures.append("overall_compare_count_legend_mismatch")
        text = " ".join([*(scene.get("texts") or []), str(ax.get("title") or ""), str(ax.get("ylabel") or ""),
                         *(ax.get("texts") or []), *(ax.get("legend") or [])])
        if not all(token in text for token in (year, "整体", "学校等权均分")):
            failures.append("overall_compare_rendered_scope_missing")
        if any(token in text for token in ("六维", "六大维度", "雷达", "数字资源", "教育教学", "数字素养", "基础设施", "教育治理", "保障机制")):
            failures.append("overall_compare_scope_widened")
        if delta is not None and regions[0] and regions[1]:
            expected_label = f"{regions[0]}减{regions[1]}：{float(delta):+.2f}分"
            if expected_label not in text:
                failures.append("overall_compare_difference_direction_or_value_mismatch")
    return {"status": "FAIL" if failures else "PASS", "scope": "rendered_region_overall_compare", "failures": sorted(set(failures))}

def validate_dimension_pair_charts(result, charts):
    pass
    scope = result.get("overview") or result
    names = list(scope.get("target_dimensions") or [])
    if scope.get("object_type") not in {"school", "region"} or len(names) != 2:
        return {"status": "NOT_APPLICABLE", "failures": []}
    rows = {row.get("dimension"): row.get("score") for row in (result.get("dimensions") or scope.get("dimensions") or [])}
    failures = []
    if len(charts) != 1 or len(set(names)) != 2 or any(rows.get(name) is None for name in names):
        failures.append("dimension_pair_scope_or_chart_incomplete")
    entity = str(scope.get("school_name") or scope.get("region_name") or "")
    years = scope.get("school_years") or []
    year = str(scope.get("year") or (years[0] if len(years) == 1 else ""))
    if not entity or not year:
        failures.append("dimension_pair_entity_or_year_missing")
    for chart in charts:
        scene = chart.get("rendered_scene") or {}
        image_data = chart.get("base64") or ""
        try:
            digest = hashlib.sha256(base64.b64decode(image_data.split(",")[-1], validate=True)).hexdigest()
            if not image_data or digest != scene.get("png_sha256"):
                failures.append("dimension_pair_rendered_image_mismatch")
        except (ValueError, TypeError):
            failures.append("dimension_pair_rendered_image_mismatch")
        axes = scene.get("axes") or []
        if scene.get("version") != "matplotlib-scene-v1" or len(axes) != 1:
            failures.append("dimension_pair_rendered_scene_missing")
            continue
        ax = axes[0]
        rendered_text = " ".join(str(value) for value in (
            [ax.get("title"), ax.get("xlabel"), ax.get("ylabel")]
            + list(ax.get("texts") or []) + list(ax.get("xticks") or [])
        ))
        if any(token not in rendered_text for token in (entity, year, names[0], names[1], "分")):
            failures.append("dimension_pair_rendered_scope_missing")
        if any(token in rendered_text for token in ("六维", "六大维度", "总体", "整体", "综合", "雷达")):
            failures.append("dimension_pair_rendered_scope_widened")
        if any(name in rendered_text for name in ({"数字资源", "教育教学", "数字素养", "基础设施", "教育治理", "保障机制"} - set(names))):
            failures.append("dimension_pair_rendered_extra_dimension")
        if ax.get("xticks") != names or ax.get("x_inverted"):
            failures.append("dimension_pair_rendered_order_mismatch")
        bars = ax.get("bars") or []
        if len(bars) != 2 or len(ax.get("xtick_positions") or []) != 2 or [bar.get("x") for bar in bars] != ax.get("xtick_positions"):
            failures.append("dimension_pair_rendered_bar_count_or_positions")
        if len(bars) == 2:
            for bar, name in zip(bars, names):
                try:
                    if bar.get("orientation") != "vertical" or abs(float(bar["height"]) - float(rows[name])) > 0.005000001:
                        failures.append("dimension_pair_rendered_value_mismatch")
                except (TypeError, ValueError, KeyError):
                    failures.append("dimension_pair_rendered_value_mismatch")
        if ax.get("projection") != "rectilinear" or any(ax.get(key) for key in ("lines", "collections", "images")):
            failures.append("dimension_pair_rendered_extra_marks")
    return {"status": "FAIL" if failures else "PASS", "scope": "rendered_dimension_pair", "failures": sorted(set(failures))}

def validate_school_item_set_total_chart(result, charts):
    pass
    scope = result.get("answer_scope") or {}
    if not (
        scope.get("operation") == "school_fact"
        and scope.get("granularity") == "item_set"
        and scope.get("chart_derived_result")
    ):
        return {"status": "NOT_APPLICABLE", "failures": []}
    failures = []
    items = result.get("item_results") or []
    derived = result.get("derived_result") or {}
    if len(items) != 2 or derived.get("operation") != "add" or derived.get("value") is None:
        failures.append("item_set_total_evidence_missing")
    expected = [item.get("target_value") for item in items] + [derived.get("value")]
    if len(charts) != 1:
        failures.append("item_set_total_chart_count")
    for chart in charts:
        scene = chart.get("rendered_scene") or {}
        image_data = chart.get("base64") or ""
        try:
            digest = hashlib.sha256(base64.b64decode(image_data.split(",")[-1], validate=True)).hexdigest()
            if not image_data or digest != scene.get("png_sha256"):
                failures.append("item_set_total_image_mismatch")
        except (ValueError, TypeError):
            failures.append("item_set_total_image_mismatch")
        axes = scene.get("axes") or []
        if scene.get("version") != "matplotlib-scene-v1" or len(axes) != 1:
            failures.append("item_set_total_scene_missing")
            continue
        ax = axes[0]
        bars = ax.get("bars") or []
        labels = ax.get("xticks") or []
        if len(bars) != 3 or len(labels) != 3 or "合计" not in str(labels[2]):
            failures.append("item_set_total_bar_or_label_missing")
            continue
        if "合计" not in str(ax.get("title") or "") or str(items[0].get("unit") or "") != str(ax.get("ylabel") or ""):
            failures.append("item_set_total_scope_missing")
        for bar, value in zip(bars, expected):
            try:
                if bar.get("orientation") != "vertical" or abs(float(bar["height"]) - float(value)) > 0.000001:
                    failures.append("item_set_total_value_mismatch")
            except (TypeError, ValueError, KeyError):
                failures.append("item_set_total_value_mismatch")
    return {"status": "FAIL" if failures else "PASS", "scope": "rendered_school_item_set_total", "failures": sorted(set(failures))}
