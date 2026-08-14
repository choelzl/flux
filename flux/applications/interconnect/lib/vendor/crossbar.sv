// -----------------------------------------------------------------------------
// Author: Cedric Hölzl
//
// Description:
//   OBI-typed wrapper around xbar_varlat (logarithmic interconnect with
//   per-output round-robin arbitration).
//
//   Each of the XBAR_NMASTER masters drives an obi_req_t; the slave-select index
//   is addr[SEL_SLICE_START +: SEL_SLICE_LENGTH], passed to xbar_varlat as its
//   bank-address field. Same-slave conflicts are resolved by the rr_arb_tree
//   inside xbar_varlat.
//
//   OBI timing: gnt is combinational, rvalid is registered (1-cycle latency), and
//   no transactions are outstanding — a port stalls until its in-flight request
//   completes (handled in addr_dec_resp_mux_varlat).
//
//   AggregateGnt=0: each master's gnt comes from its individually addressed slave
//   rather than the OR of all slave grants, as the masters may be shared outputs
//   of an upstream crossbar.
//
// Parameters:
//   XBAR_NMASTER     — number of OBI master ports
//   XBAR_NSLAVE      — number of OBI slave ports (power of 2)
//   SEL_SLICE_START  — LSB of the address slice used for slave selection
//   SEL_SLICE_LENGTH — width of that slice; 2^SEL_SLICE_LENGTH == XBAR_NSLAVE
// -----------------------------------------------------------------------------

`include "obi_pkg.sv"

module crossbar
    import obi_pkg::*;
#(
    parameter int XBAR_NMASTER     = 3,
    parameter int XBAR_NSLAVE      = 6,
    parameter int SEL_SLICE_START  = 0,
    parameter int SEL_SLICE_LENGTH = 2
) (
    input logic clk_i,
    input logic rst_ni,

    input  obi_req_t  [XBAR_NMASTER-1:0] master_req_i,
    output obi_resp_t [XBAR_NMASTER-1:0] master_resp_o,

    output obi_req_t  [XBAR_NSLAVE-1:0] slave_req_o,
    input  obi_resp_t [XBAR_NSLAVE-1:0] slave_resp_i
);

    localparam int unsigned LOG_XBAR_NSLAVE = XBAR_NSLAVE > 1 ? $clog2(XBAR_NSLAVE) : 1;
    localparam int unsigned REQ_AGG_DATA_WIDTH = $bits(obi_req_t) - 1;
    localparam int unsigned RESP_AGG_DATA_WIDTH = `OBI_DATA_WIDTH;

    logic [XBAR_NMASTER-1:0][   LOG_XBAR_NSLAVE-1:0] port_sel;
    logic [XBAR_NMASTER-1:0]                         master_req_req;
    logic [XBAR_NMASTER-1:0][REQ_AGG_DATA_WIDTH-1:0] master_req_data;
    logic [XBAR_NMASTER-1:0]                         master_resp_gnt;
    logic [XBAR_NMASTER-1:0]                         master_resp_rvalid;
    logic [XBAR_NMASTER-1:0][   `OBI_DATA_WIDTH-1:0] master_resp_rdata;

    logic [ XBAR_NSLAVE-1:0]                         slave_req_req;
    logic [ XBAR_NSLAVE-1:0][REQ_AGG_DATA_WIDTH-1:0] slave_req_out_data;
    logic [ XBAR_NSLAVE-1:0]                         slave_resp_gnt;
    logic [ XBAR_NSLAVE-1:0]                         slave_resp_rvalid;
    logic [ XBAR_NSLAVE-1:0][   `OBI_DATA_WIDTH-1:0] slave_resp_rdata;

    for (genvar i = 0; i < XBAR_NMASTER; i++) begin : gen_sel_signal
        assign port_sel[i] = master_req_i[i].addr[SEL_SLICE_START+:SEL_SLICE_LENGTH];
    end

    for (genvar i = 0; i < XBAR_NMASTER; i++) begin : gen_unroll_master
        assign master_req_req[i] = master_req_i[i].req;
        assign master_req_data[i] = {
            master_req_i[i].we, master_req_i[i].be, master_req_i[i].addr, master_req_i[i].wdata
        };
        assign master_resp_o[i].gnt = master_resp_gnt[i];
        assign master_resp_o[i].rvalid = master_resp_rvalid[i];
        assign master_resp_o[i].rdata = master_resp_rdata[i];
    end

    for (genvar i = 0; i < XBAR_NSLAVE; i++) begin : gen_unroll_slave
        assign slave_req_o[i].req = slave_req_req[i];
        assign {slave_req_o[i].we, slave_req_o[i].be,
                slave_req_o[i].addr, slave_req_o[i].wdata} = slave_req_out_data[i];
        assign slave_resp_gnt[i] = slave_resp_i[i].gnt;
        assign slave_resp_rvalid[i] = slave_resp_i[i].rvalid;
        assign slave_resp_rdata[i] = slave_resp_i[i].rdata;
    end

    xbar_varlat #(
        .AggregateGnt (0),
        .NumIn        (XBAR_NMASTER),
        .NumOut       (XBAR_NSLAVE),
        .ReqDataWidth (REQ_AGG_DATA_WIDTH),
        .RespDataWidth(RESP_AGG_DATA_WIDTH)
    ) xbar_varlat_i (
        .clk_i,
        .rst_ni,
        .req_i  (master_req_req),
        .add_i  (port_sel),
        .wdata_i(master_req_data),
        .gnt_o  (master_resp_gnt),
        .rdata_o(master_resp_rdata),
        .rr_i   ('0),
        .vld_o  (master_resp_rvalid),
        .gnt_i  (slave_resp_gnt),
        .req_o  (slave_req_req),
        .vld_i  (slave_resp_rvalid),
        .wdata_o(slave_req_out_data),
        .rdata_i(slave_resp_rdata)
    );

endmodule
