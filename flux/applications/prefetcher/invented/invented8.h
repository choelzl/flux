#ifndef INVENTED8_H
#define INVENTED8_H
#include <cstdint>
#include <vector>
#include <iostream>
#include <algorithm>
#include "prefetcher.h"
#include "champsim.h"

namespace knob { extern uint32_t invented8_max; extern uint32_t invented8_conf; }

class Invented8Prefetcher : public Prefetcher
{
   struct Entry { uint64_t last_line; uint32_t count; uint64_t delta; };
   std::vector<Entry> table_;
   uint64_t issued_ = 0;

   public:
   Invented8Prefetcher(std::string type) : Prefetcher(type), table_(knob::invented8_max) {
      for (uint32_t i = 0; i < knob::invented8_max; i++) {
         table_[i].last_line = 0xFFFFFFFFFFFFFFFF;
         table_[i].count = 0;
         table_[i].delta = 0;
      }
   }

   void invoke_prefetcher(uint64_t pc, uint64_t address, uint8_t, uint8_t,
                          std::vector<uint64_t> &pref_addr)
   {
      uint64_t line = address >> LOG2_BLOCK_SIZE;
      uint64_t pc_hash = (pc >> 2) & (knob::invented8_max - 1);
      Entry &e = table_[pc_hash];

      if (e.last_line == 0xFFFFFFFFFFFFFFFF) {
         e.last_line = line;
         e.count = 1;
         e.delta = 0;
         return;
      }

      int64_t delta = (int64_t)line - (int64_t)e.last_line;

      if (delta == (int64_t)e.delta) {
         e.count++;
      } else {
         e.delta = delta;
         e.count = 1;
      }
      e.last_line = line;

      if (e.count >= knob::invented8_conf && delta != 0) {
         uint64_t next_line = line + delta;
         uint64_t next_addr = next_line << LOG2_BLOCK_SIZE;
         if (next_addr != address) {
            if (std::find(pref_addr.begin(), pref_addr.end(), next_addr) == pref_addr.end()) {
               pref_addr.push_back(next_addr);
               issued_++;
            }
         }
      }
   }

   void dump_stats() { std::cout << "invented8_issued " << issued_ << std::endl; }
   void print_config() { std::cout << "invented8_max " << knob::invented8_max << " conf " << knob::invented8_conf << std::endl; }
};
#endif
