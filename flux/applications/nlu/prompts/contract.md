INTERFACE CONTRACT (the harness instantiates exactly this; deviation = compile refusal):
* shared style : module nlu      (input wire clk, input wire [15:0] x,
                                  input wire [2:0] op, output wire [15:0] y);
  opcodes: {opcodes}
* per-op style : one module per operator, named nlu_<op>:
                 module nlu_<op> (input wire clk, input wire [15:0] x,
                                  output wire [15:0] y);
* Combinational (latency 0) designs ignore clk (the port must still exist).
* A pipelined design of latency L takes one x every cycle and answers exactly L
  cycles later -- no handshake, no reset, no stalls; internal registers only.
* Synthesizable SystemVerilog: no initial blocks driving logic, no delays, no DPI;
  ROM/LUT contents as case statements or localparam arrays are fine.
* x and y are IEEE FP16 bit patterns. Subnormals are real inputs and real outputs.
* SAFE SUBSET (this campaign's own syntax refusals bred these rules): only
  assign / always @* / always @(posedge clk) / case / localparam / function;
  NO typedef, struct, interface, generate, or wire declarations inside begin/end
  blocks -- declare every wire/reg at module scope. Every sized literal complete
  (16'h3C00, never 16'h or 5'd-14; negatives via -16'sd14 or explicit two's
  complement). One statement per line, every statement ends with ;

A COMPILING SKELETON to start from (it passes the tools as-is and returns NaN for
everything -- so it fails the gate until you implement real math; extend it, do
not fight it):
```verilog
module nlu(input wire clk, input wire [15:0] x, input wire [2:0] op,
           output reg [15:0] y);
  wire        sign = x[15];
  wire [4:0]  ex   = x[14:10];
  wire [9:0]  mant = x[9:0];
  always @* begin
    case (op)
      3'd0: y = 16'h7e00;  // exp      -- replace with real math
      3'd1: y = 16'h7e00;  // log
      3'd2: y = 16'h7e00;  // sigmoid
      3'd3: y = 16'h7e00;  // tanh
      3'd4: y = 16'h7e00;  // gelu
      3'd5: y = 16'h7e00;  // recip
      3'd6: y = 16'h7e00;  // rsqrt
      default: y = 16'h7e00;
    endcase
  end
endmodule
```