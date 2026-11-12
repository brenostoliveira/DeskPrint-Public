// Modified by DeskPrint Studio, 2026-11-02. Offline synthetic export regression.
#include <catch2/catch_test_macros.hpp>

#include "libslic3r/Config.hpp"
#include "libslic3r/Exception.hpp"
#include "libslic3r/GCodeReader.hpp"
#include "libslic3r/Model.hpp"
#include "libslic3r/Print.hpp"
#include "libslic3r/PrintConfig.hpp"
#include "libslic3r/TriangleMesh.hpp"
#include "test_helpers.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdlib>
#include <fstream>
#include <string>
#include <vector>

using namespace Slic3r;
using namespace Slic3r::Test;

static const char *native_change =
    "G2 Z{z_after_toolchange + 0.4} I0.86 J0.86 P1 F10000 ; native lift\n"
    "G1 X260 Y180 F30000\nG1 Z{z_after_toolchange} F600\n";

static DynamicPrintConfig hi_config(bool compact, bool relative_e, bool repeated)
{
    return multifilament_config(2, {
        {"printer_model", "Creality Hi"}, {"gcode_flavor", "klipper"},
        {"single_extruder_multi_material", true}, {"manual_filament_change", false},
        {"wipe_tower_type", "type2"}, {"enable_prime_tower", true},
        {"wipe_tower_no_sparse_layers", compact}, {"wipe_tower_sparse_layers_combination", false},
        {"wipe_tower_x", 40}, {"wipe_tower_y", 150}, {"prime_tower_width", 35},
        {"printable_area", "0x0,260x0,260x260,0x260"},
        {"change_filament_gcode", native_change}, {"enable_filament_ramming", false},
        {"use_relative_e_distances", relative_e}, {"gcode_comments", true},
        {"layer_height", 0.2}, {"initial_layer_print_height", 0.2},
        {"sparse_infill_filament_id", 1}, {"internal_solid_filament_id", 1},
        {"top_shell_layers", 3}, {"bottom_shell_layers", 3},
        {"top_shell_thickness", 0.}, {"bottom_shell_thickness", 0.},
        {"top_surface_filament_id", 2}, {"bottom_surface_filament_id", 2},
        {"outer_wall_filament_id", repeated ? 2 : 1},
        {"inner_wall_filament_id", repeated ? 2 : 1}, {"timelapse_type", "0"},
        {"enable_wrapping_detection", false}, {"printable_height", 300}
    });
}

static std::string export_hi(const DynamicPrintConfig &config)
{
    Print print;
    Model model;
    init_print({make_cube(10., 10., 4.)}, print, model, config);
    print.apply(model, config); // retain the two-filament tower after normalization
    return gcode(print);
}

// Compare extrusion instructions and tool identities, not time estimates or a
// byte-for-byte golden file: additional safe Z travel legitimately changes timing.
static std::vector<std::string> extrusion_and_tools(const std::string &gcode)
{
    std::vector<std::string> result;
    GCodeReader reader;
    reader.parse_buffer(gcode, [&](GCodeReader &, const GCodeReader::GCodeLine &line) {
        const std::string cmd(line.cmd());
        if ((!cmd.empty() && cmd.front() == 'T') || line.has_e()) {
            std::string item = cmd;
            if (line.has_e())
                item += " E" + std::to_string(line.e());
            result.push_back(item);
        }
    });
    return result;
}

static void check_cfs_crossings(bool compact, bool relative_e, bool repeated, double z_offset)
{
    auto config = hi_config(compact, relative_e, repeated);
    config.set_deserialize_strict({{"z_offset", std::to_string(z_offset)}});
    const std::string safe = export_hi(config);
    auto baseline_config = config;
    baseline_config.set_deserialize_strict({{"printer_model", "Unmodified test printer"}});
    const std::string baseline = export_hi(baseline_config);
    if (const char *directory = std::getenv("DPS_CFS_FIXTURE_DIR")) {
        const std::string stem = std::string(directory) + "/cfs-" + std::to_string(compact) +
            std::to_string(relative_e) + std::to_string(repeated) + "-" + std::to_string(z_offset);
        std::ofstream(stem + "-safe.gcode", std::ios::binary) << safe;
        std::ofstream(stem + "-baseline.gcode", std::ios::binary) << baseline;
    }
    CHECK(extrusion_and_tools(safe) == extrusion_and_tools(baseline));
    // Negative control: this synthetic compacted tower must expose the original
    // low station crossing, otherwise the clearance assertions could be vacuous.
    if (compact && !repeated) {
        double object_z = z_offset;
        bool tower = false;
        size_t unsafe = 0;
        GCodeReader original;
        original.apply_config(baseline_config);
        original.parse_buffer(baseline, [&](GCodeReader &state, const GCodeReader::GCodeLine &line) {
            const std::string comment(line.comment());
            if (comment.find("TYPE:") != std::string::npos || comment.find("FEATURE:") != std::string::npos)
                tower = comment.find("tower") != std::string::npos || comment.find("Tower") != std::string::npos;
            if (line.extruding(state) && !tower)
                object_z = std::max(object_z, double(line.new_Z(state)));
            if (tower && line.has_x() && std::abs(line.x() - 260.f) < 0.001f &&
                double(line.new_Z(state)) + 0.001 < object_z)
                ++unsafe;
        });
        REQUIRE(unsafe > 0);
    }

    size_t station_visits = 0, return_lifts = 0, tower_restores = 0;
    double highest_object = z_offset;
    bool on_tower = false, returning = false;
    float station_x = 0, station_y = 0;
    GCodeReader reader;
    reader.apply_config(config);
    reader.parse_buffer(safe, [&](GCodeReader &state, const GCodeReader::GCodeLine &line) {
        const std::string comment(line.comment());
        if (comment.find("TYPE:") != std::string::npos || comment.find("FEATURE:") != std::string::npos)
            on_tower = comment.find("tower") != std::string::npos || comment.find("Tower") != std::string::npos;
        if (line.extruding(state) && !on_tower)
            highest_object = std::max(highest_object, double(line.new_Z(state)));
        if (on_tower && line.cmd() == "G1" && line.has_x() && line.has_y() &&
            std::abs(line.x() - 260.f) < 0.001f && std::abs(line.y() - 180.f) < 0.001f) {
            ++station_visits;
            CHECK(double(line.new_Z(state)) + 0.001 >= highest_object + 0.4);
            station_x = line.x(); station_y = line.y();
        }
        if (comment.find("DeskPrint CFS return clearance") != std::string::npos) {
            ++return_lifts;
            CHECK(double(line.new_Z(state)) + 0.001 >= highest_object + 0.4);
            returning = true;
        } else if (returning && (line.has_x() || line.has_y())) {
            CHECK(double(line.new_Z(state)) + 0.001 >= highest_object + 0.4);
            CHECK_FALSE(line.extruding(state));
            returning = false;
        }
        if (comment.find("Force restore layer Z") != std::string::npos) {
            ++tower_restores;
            CHECK_FALSE(returning);
            CHECK(std::abs(state.x() - station_x) + std::abs(state.y() - station_y) > 1.f);
        }
    });
    REQUIRE(station_visits > 0);
    CHECK(return_lifts == station_visits);
    CHECK(tower_restores == station_visits);
    CHECK_FALSE(returning);
}

TEST_CASE("Hi CFS compact relative late change", "[CrealityHiCfsTravel]")
{ check_cfs_crossings(true, true, false, 0.); }
TEST_CASE("Hi CFS compact absolute offset late change", "[CrealityHiCfsTravel]")
{ check_cfs_crossings(true, false, false, 0.15); }
TEST_CASE("Hi CFS compact relative repeated changes", "[CrealityHiCfsTravel]")
{ check_cfs_crossings(true, true, true, 0.15); }
TEST_CASE("Hi CFS compact absolute repeated changes", "[CrealityHiCfsTravel]")
{ check_cfs_crossings(true, false, true, 0.); }
TEST_CASE("Hi CFS normal relative late change", "[CrealityHiCfsTravel]")
{ check_cfs_crossings(false, true, false, 0.); }
TEST_CASE("Hi CFS normal absolute repeated changes", "[CrealityHiCfsTravel]")
{ check_cfs_crossings(false, false, true, 0.15); }

TEST_CASE("Hi CFS refuses unknown station macros and insufficient Z clearance", "[CrealityHiCfsTravel]")
{
    auto config = hi_config(true, true, false);
    SECTION("custom code is not silently rewritten") {
        config.set_deserialize_strict({{"change_filament_gcode", "G1 X260 Y180\n"}});
        CHECK_THROWS_AS(export_hi(config), Slic3r::RuntimeError);
    }
    SECTION("no room for the native 0.4 mm lift") {
        config.set_deserialize_strict({{"printable_height", 4.1}});
        CHECK_THROWS_AS(export_hi(config), Slic3r::RuntimeError);
    }
    SECTION("a renamed display preset keeps the stable model capability") {
        config.set_deserialize_strict({{"printer_settings_id", "A printer chosen by its user"}});
        CHECK(export_hi(config).find("DeskPrint CFS return clearance") != std::string::npos);
    }
}
