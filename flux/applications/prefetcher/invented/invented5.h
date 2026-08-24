#ifndef INVENTED5_H
#define INVENTED5_H
#include <cstdint>
#include <unordered_map>
#include <vector>
#include <iostream>
#include <algorithm>
#include "prefetcher.h"
#include "champsim.h"

namespace knob { extern uint32_t invented5_max_trackers; extern uint32_t invented5_conf; }

class Invented5Prefetcher : public Prefetcher {
    struct Entry { int64_t last; int64_t stride; int32_t conf; };
    std::unordered_map<uint64_t, Entry> map_;
public:
    Invented5Prefetcher(std::string type) : Prefetcher(type) {}
    void invoke_prefetcher(uint64_t pc, uint64_t address, uint8_t, uint8_t, std::vector<uint64_t> &pref_addr) {
        uint32_t tag = static_cast<uint32_t>((pc >> 12) ^ ((address >> 12) & 0x3F));
        int32_t idx = (int32_t)((address >> LOG2_BLOCK_SIZE) & 0x3F);
        auto it = map_.find(tag);
        if (it == map_.end()) {
            if (map_.size() >= knob::invented5_max_trackers) {
                auto to_erase = map_.begin();
                while (to_erase != map_.end() && to_erase->second.conf != 0) ++to_erase;
                if (to_erase == map_.end()) to_erase = map_.begin();
                map_.erase(to_erase);
            }
            map_[tag] = {idx, 0, 0};
            return;
        }
        Entry &e = it->second;
        int64_t delta = idx - e.last;
        if (delta != 0) {
            if (delta == e.stride) e.conf = (e.conf + 1 < 1000 ? e.conf + 1 : 1000);
            else { e.stride = delta; e.conf = 1; }
            if (e.conf >= knob::invented5_conf) {
                for (int32_t i = 1; i < 4; ++i) {
                    int32_t target = (idx + i * e.stride) & 0x3F;
                    uint64_t base = (address & ~(uint64_t(0xF) << 12)) | ((uint64_t)target << LOG2_BLOCK_SIZE);
                    pref_addr.push_back(base | (address & (uint64_t(1) << LOG2_BLOCK_SIZE) - 1));
                }
            }
        }
        e.last = idx;
    }
    void dump_stats() {}
    void print_config() {}
};
#endif
