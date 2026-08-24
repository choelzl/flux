THE STAGE: a PYTHON PROTOTYPE of `{part}`. The RTL module nlu_{part} is TRANSPILED by the
harness from the prototype once it passes all 65536 inputs -- you never write Verilog here;
a reply carrying SystemVerilog is refused unread. Tables are rom(...)/slope_rom(...) at
module level (the transpiler turns them into ROM functions); the sheet's methods, range
reductions and saturation thresholds above apply as written, in integer arithmetic.