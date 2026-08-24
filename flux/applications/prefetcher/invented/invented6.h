#ifndef INVENTED6_H
#define INVENTED6_H
#include <cstdint>
#include <vector>
#include <iostream>
#include <unordered_map>
#include "prefetcher.h"
#include "champsim.h"

namespace knob { extern uint32_t invented6_degree; extern uint32_t invented6_conf; }

class Invented6Prefetcher : public Prefetcher
{
   struct E { int64_t prev; int64_t stride; uint32_t conf; };
   std::unordered_map<uint64_t, E> m_;
   uint64_t n_ = 0;
public:
   Invented6Prefetcher(std::string type) : Prefetcher(type) {}
   void invoke_prefetcher(uint64_t pc, uint64_t address, uint8_t, uint8_t,
                          std::vector<uint64_t> &pref_addr)
   {
      uint64_t line = address >> LOG2_BLOCK_SIZE;
      uint64_t key = (pc ^ (line >> 6)) << 6 | (line & 63);
      auto it = m_.find(key);
      if (it == m_.end()) { if (m_.size() >= 65536) m_.clear(); m_[key] = {-1, 0, 0}; return; }
      E &e = it->second;
      int64_t d = (int64_t)line - e.prev;
      if (e.prev >= 0) {
         if (d == e.stride && e.conf < 8) e.conf++;
         else e.conf = 0;
      }
      e.prev = line; e.stride = d;
      if (e.conf >= (uint32_t)knob::invented6_conf && e.stride != 0)
         for (uint32_t k = 1; k <= knob::invented6_degree; k++) {
            uint64_t t = line + (uint64_t)k * (uint64_t)e.stride;
            if ((t >> 6) != (line >> 6)) pref_addr.push_back(t << LOG2_BLOCK_SIZE);
            n_++;
         }
   }
   void dump_stats() { std::cout << "invented6_issued " << n_ << std::endl; }
   void print_config() { std::cout << "invented6_conf " << knob::invented6_conf
                                   << " degree " << knob::invented6_degree << std::endl; }
};
#endif
