# The L2 prefetcher configuration: what an `.ini` may say

The file is ChampSim knobs, one `name = value` per line. `bingo_default.ini` beside this file is
the shipped configuration (Bingo alone); start from it.

## The stack

`l2c_prefetcher_types = bingo[,partner,...]`: Bingo always, plus any partners beside it in the L2
slot: `sms`, `ampm`, `stride`, `streamer`, `spp_ppf_dev`, `power7`, `sandbox`, `spp_dev2`,
`ipcp`. `scooby`, `mlop` and `next_line` crash beside Bingo. A partner's knobs are read only
when it is in the stack; a knob left out takes its shipped value.

## Bingo (shipped value in brackets)

- `bingo_region_size` [2048]: bytes learned per region, a power of two, 64..4096.
  `bingo_pattern_len` must equal region_size / 64 (bingo.cc aborts otherwise).
- `bingo_ft_size` [64], `bingo_at_size` [128]: regions observed and accumulated at once (16-way).
- `bingo_pht_size` [4096], `bingo_pht_ways` [16]: the learned-pattern table; size = ways x sets,
  sets a power of two. Usually where both the speedup and the storage are.
- `bingo_pf_streamer_size` [128]: outstanding prefetches (16-way).
- `bingo_pc_width` [16], `bingo_min_addr_width` [5], `bingo_max_addr_width` [16]: the PC and
  address bits that key the pattern table, 0..30; max >= min; pc + min > 0.
- `bingo_l2c_thresh` [0.8]: confidence before the L2 prefetches, 0..1, lower is more aggressive;
  it costs no storage.
- Keep `bingo_debug_level = 0`, `bingo_l1d_thresh = 1.01`, `bingo_llc_thresh = 0.05`,
  `bingo_pc_address_fill_level = L2`.

Every table keeps a tag: key bits - log2(sets) >= 0, where the filter/accumulation key is
48 - log2(region_size), the streamer's 64 - log2(region_size) and the pattern table's
pc_width + max_addr_width.

## Storage (`storage_bytes`)

Each Bingo table costs entries x (tag + payload + valid + LRU) bits. The pattern table is 31 KB
of the shipped 35 KB. The accumulation payload grows with pattern_len, the streamer's is
2 x pattern_len, the pattern table's is pattern_len. Partners are not counted.

## Partners' knobs (shipped value in brackets)

- sms: `sms_pref_degree` [4], `sms_pht_size` [2048], `sms_pht_assoc` [16], `sms_region_size`
  [4096], `sms_ft_size` [64], `sms_at_size` [32], `sms_pref_buffer_size` [256]
- ampm: `ampm_pref_degree` [4], `ampm_pred_degree` [4], `ampm_pb_size` [64],
  `ampm_pref_buffer_size` [256]
- stride: `stride_pref_degree` [2], `stride_num_trackers` [256]
- streamer: `streamer_pref_degree` [5], `streamer_num_trackers` [64]
- spp_ppf_dev: `ppf_perc_threshold_hi` [-5], `ppf_perc_threshold_lo` [-15]
- power7: `power7_default_streamer_degree` [4], `power7_explore_epoch` [20000],
  `power7_exploit_epoch` [200000]
- sandbox: `sandbox_pref_degree` [4], `sandbox_num_access_in_phase` [256],
  `sandbox_bloom_filter_size` [2048], `sandbox_num_cycle_offsets` [4]
- spp_dev2, ipcp: no knobs
