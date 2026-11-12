// Modified by DeskPrinter Studio on 2026-10-31: apply per-job CFS tool mapping.
// Same AGPL-3.0 license as the surrounding OrcaSlicer source.
#pragma once
#include <nlohmann/json.hpp>
#include <algorithm>
#include <cctype>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace Slic3r {
// Pure preparation helper. No network, firmware state or persistent profile edits.
class MoonrakerToolMapping {
    std::vector<int> slots;
    std::set<int> used;
    static nlohmann::json parse(const std::string& raw) {
        auto value=nlohmann::json::parse(raw,nullptr,false);
        if (!value.is_array() || value.empty() || value.size()>256)
            throw std::runtime_error("DeskPrinter: missing or invalid CFS filament mapping. Select the spools before sending.");
        return value;
    }
    int physical(int tool) const {
        if (tool<0 || tool>=static_cast<int>(slots.size()) || slots[tool]<0 || slots[tool]>15)
            throw std::runtime_error("DeskPrinter: an active filament has no valid CFS slot. Select its spool before sending.");
        return slots[tool];
    }
    // Match only a real T command, never temperatures, comments or substrings.
    static int tool(const std::string& line, size_t& begin, size_t& end) {
        size_t pos=line.find_first_not_of(" \t\r");
        if (pos==std::string::npos || line[pos]==';')return -1;
        if (line[pos]=='N' || line[pos]=='n') {
            auto next=line.find_first_of(" \t",pos);
            if (next==std::string::npos)return -1;
            pos=line.find_first_not_of(" \t",next);
            if (pos==std::string::npos || (line[pos]!='T' && line[pos]!='t'))return -1;
            throw std::runtime_error("DeskPrinter: numbered tool commands must be re-sliced before CFS mapping.");
        }
        if (line[pos]!='T' && line[pos]!='t')return -1;
        begin=++pos;end=pos;
        if (pos<line.size() && std::isalpha(static_cast<unsigned char>(line[pos])))return -1;
        while (end<line.size() && std::isdigit(static_cast<unsigned char>(line[end])))++end;
        if (end==begin || end-begin>3 || (end<line.size() && !std::isspace(static_cast<unsigned char>(line[end])) && line[end]!=';'))
            throw std::runtime_error("DeskPrinter: unsupported tool command in the print file.");
        return std::stoi(line.substr(begin,end-begin));
    }
public:
    MoonrakerToolMapping(const std::string& v0, const std::string& v1) {
        auto mapping=parse(v0);
        for (const auto& slot:mapping) {
            if (!slot.is_number_integer() || slot.get<long long>() < -1 || slot.get<long long>()>15)
                throw std::runtime_error("DeskPrinter: CFS slot outside the supported range.");
            slots.push_back(slot.get<int>());
        }
        if (!v1.empty()) {
            auto mapping2=parse(v1);
            if (mapping2.size()!=slots.size())throw std::runtime_error("DeskPrinter: inconsistent CFS mapping versions.");
            for (size_t i=0;i<slots.size();++i) {
                const auto& item=mapping2[i];
                if (!item.is_object() || !item.contains("ams_id") || !item.contains("slot_id") ||
                    !item["ams_id"].is_number_integer() || !item["slot_id"].is_number_integer())
                    throw std::runtime_error("DeskPrinter: invalid CFS box/slot mapping.");
                const auto box=item["ams_id"].get<long long>(), slot=item["slot_id"].get<long long>();
                if (slots[i]>=0 && (box<0 || box>3 || slot<0 || slot>3 || box*4+slot!=slots[i]))
                    throw std::runtime_error("DeskPrinter: CFS mapping disagrees about the selected spool.");
            }
        }
    }
    void inspect(const std::string& line) {
        size_t begin=0,end=0;int id=tool(line,begin,end);
        if (id>=0) {physical(id);used.insert(id);}
    }
    bool has_tools() const {return !used.empty();}
    std::string rewrite(const std::string& line) const {
        size_t begin=0,end=0;int id=tool(line,begin,end);
        if (id>=0)return line.substr(0,begin)+std::to_string(physical(id))+line.substr(end);
        // Keep preview colors consistent with the remapped tools. Do not edit
        // temperatures, extrusion, movement or the user's saved slicer profile.
        for (const std::string key:{"; filament_colour = ","; extruder_colour = "}) {
            if (line.rfind(key,0)!=0)continue;
            std::vector<std::string> colors;size_t start=key.size();
            do {
                size_t stop=line.find(';',start);
                colors.push_back(line.substr(start,stop==std::string::npos?stop:stop-start));
                if (stop==std::string::npos)break;
                start=stop+1;
            } while (true);
            if (!colors.empty() && !colors.back().empty() && colors.back().back()=='\r')colors.back().pop_back();
            auto output=colors;
            for (int logical:used) {
                if (logical>=static_cast<int>(colors.size()))throw std::runtime_error("DeskPrinter: filament palette is incomplete.");
                int slot=physical(logical);
                if (output.size()<=static_cast<size_t>(slot))output.resize(slot+1,"#FFFFFF");
                output[slot]=colors[logical];
            }
            std::string result=key;
            for (size_t i=0;i<output.size();++i) {if(i)result+=';';result+=output[i];}
            if (!line.empty() && line.back()=='\r')result+='\r';
            return result;
        }
        return line;
    }
};
}
