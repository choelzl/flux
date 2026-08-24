#ifndef INVENTED1_H
#define INVENTED1_H
#include <cstdint>
#include <vector>
#include <iostream>
#include "prefetcher.h"
#include "champsim.h"
namespace knob { extern uint32_t invented1_conf; extern uint32_t invented1_cap; }
class Invented1Prefetcher : public Prefetcher
{
   struct S { uint64_t prev, last; uint32_t conf; };
   S s_;
   uint64_t pc_;
   uint64_t issued_ = 0;
   inline void reset() { s_.prev = s_.last = 0; s_.conf = 0; }
public:
   Invented1Prefetcher(std::string t) : Prefetcher(t) { pc_ = ~0ull; reset(); }
   void invoke_prefetcher(uint64_t pc, uint64_t address, uint8_t, uint8_t,
                          std::vector<uint64_t> &p)
   {
      uint64_t ln = address >> LOG2_BLOCK_SIZE;
      if (pc != pc_) { pc_ = pc; reset(); }
      if (s_.conf >= knob::invented1_conf && s_.prev && s_.last && s_.last != s_.prev)
         p.push_back(((s_.last + (s_.last - s_.prev)) << LOG2_BLOCK_SIZE));
      if (s_.last && ln != s_.last) {
         if (s_.prev && ln - s_.last == s_.last - s_.prev) {
            if (s_.conf < 0xFFFFu) s_.conf++;
         } else s_.conf = 0;
         s_.prev = s_.last;
      } else s_.prev = 0;
      s_.last = ln;
      if (p.size()) issued_++;
   }
   void dump_stats() { std::cout << "invented1_issued " << issued_ << std::endl; }
   void print_config() { std::cout << "invented1_conf " << knob::invented1_conf
                                << " invented1_cap " << knob::invented1_cap << std::endl; }
};
#endif
