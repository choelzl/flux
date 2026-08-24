// -----------------------------------------------------------------------------
// Author: Cedric Hölzl
//
// Description:
//   OBI package — shared request/response struct types (simplified
//   single-channel OBI).
//
//   Data-path widths are set by two compile-time macros, overridable at
//   elaboration (-DOBI_DATA_WIDTH=<n> / -DOBI_BE_WIDTH=<n>):
//     OBI_DATA_WIDTH — data bus width in bits    (default 128, i.e. 16-byte access)
//     OBI_BE_WIDTH   — byte-enable width in bits (default OBI_DATA_WIDTH/8, one bit per byte)
// -----------------------------------------------------------------------------

`ifndef OBI_PKG_SV
`define OBI_PKG_SV

`ifndef OBI_DATA_WIDTH
`define OBI_DATA_WIDTH 128
`endif
`ifndef OBI_BE_WIDTH
`define OBI_BE_WIDTH (`OBI_DATA_WIDTH / 8)
`endif

package obi_pkg;

    typedef struct packed {
        logic                       req;
        logic                       we;
        logic [`OBI_BE_WIDTH-1:0]   be;
        logic [31:0]                addr;
        logic [`OBI_DATA_WIDTH-1:0] wdata;
    } obi_req_t;

    typedef struct packed {
        logic                       gnt;
        logic                       rvalid;
        logic [`OBI_DATA_WIDTH-1:0] rdata;
    } obi_resp_t;

endpackage

`endif
