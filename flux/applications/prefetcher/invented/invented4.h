#ifndef INVENTED4_H
#define INVENTED4_H
#include <cstdint>
#include <vector>
#include <iostream>
#include "prefetcher.h"
#include "champsim.h"

namespace knob { extern uint32_t invented4_degree; extern uint32_t invented4_stride_max; }

class Invented4Prefetcher : public Prefetcher
{
   struct S { uint64_t last_line; int64_t stride; uint32_t conf; };
   static const int N = 1024;
   S tab_[N];
   uint64_t issued_ = 0;
   inline uint32_t idx(uint64_t pc) {
      return (pc * 2654435761u) & (N - 1);
   }
public:
   Invented4Prefetcher(std::string type) : Prefetcher(type) {
      for (int i = 0; i < N; i++) tab_[i] = {0, 0, 0};
   }
   void invoke_prefetcher(uint64_t pc, uint64_t address, uint8_t, uint8_t,
                          std::vector<uint64_t> &pref_addr)
   {
      uint64_t line = address >> LOG2_BLOCK_SIZE;
      S &t = tab_[idx(pc)];
      int64_t d = (int64_t)line - (int64_t)t.last_line;
      if (d != 0 && d == t.stride) {
         if (t.conf < 4) t.conf++;
      } else {
         t.stride = d;
         t.conf = 0;
      }
      t.last_line = line;
      
      if (t.conf >= 2 && t.stride != 0 && t.stride <= (int64_t)knob::invented4_stride_max) {
         for (uint32_t k = 1; k <= knob::invented4_degree; k++) {
            uint64_t pl = line + k * t.stride;
            pref_addr.push_back(pl << LOG2_BLOCK_SIZE);
            issued_++;
         }
      }
   }
   void dump_stats() { std::cout << "invented4_issued " << issued_ << std::endl; }
   void print_config() { std::cout << "invented4_degree " << knob::invented4_degree << std::endl; }
};
#endif
