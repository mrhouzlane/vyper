from vyper import ast as vy_ast
from vyper.exceptions import (
    ConstancyViolation,
    StructureException,
    TypeCheckFailure,
)
from vyper.parser.lll_node import LLLnode
from vyper.parser.parser_utils import getpos, pack_arguments, unwrap_location
from vyper.types import (
    BaseType,
    ByteArrayLike,
    ListType,
    TupleLike,
    get_size_of_type,
)


def external_call(node, context, interface_name, contract_address, pos, value=None, gas=None):
    from vyper.parser.expr import Expr

    if value is None:
        value = 0
    if gas is None:
        gas = "gas"
    if contract_address.value == "address":
        raise StructureException("External calls to self are not permitted.", node)
    method_name = node.func.attr
    sig = context.sigs[interface_name][method_name]
    inargs, inargsize, _ = pack_arguments(
        sig, [Expr(arg, context).lll_node for arg in node.args], context, node.func,
    )
    output_placeholder, output_size, returner = get_external_call_output(sig, context)
    sub = [
        "seq",
        ["assert", ["extcodesize", contract_address]],
        ["assert", ["ne", "address", contract_address]],
    ]
    if context.is_constant() and not sig.const:
        raise ConstancyViolation(
            f"May not call non-constant function '{method_name}' within {context.pp_constancy()}."
            " For asserting the result of modifiable contract calls, try assert_modifiable.",
            node,
        )

    if context.is_constant() or sig.const:
        sub.append(
            [
                "assert",
                [
                    "staticcall",
                    gas,
                    contract_address,
                    inargs,
                    inargsize,
                    output_placeholder,
                    output_size,
                ],
            ]
        )
    else:
        sub.append(
            [
                "assert",
                [
                    "call",
                    gas,
                    contract_address,
                    value,
                    inargs,
                    inargsize,
                    output_placeholder,
                    output_size,
                ],
            ]
        )
    sub.extend(returner)
    o = LLLnode.from_list(sub, typ=sig.output_type, location="memory", pos=getpos(node))
    return o


def get_external_call_output(sig, context):
    if not sig.output_type:
        return 0, 0, []
    output_placeholder = context.new_placeholder(typ=sig.output_type)
    output_size = get_size_of_type(sig.output_type) * 32
    if isinstance(sig.output_type, BaseType):
        returner = [0, output_placeholder]
    elif isinstance(sig.output_type, ByteArrayLike):
        returner = [0, output_placeholder + 32]
    elif isinstance(sig.output_type, TupleLike):
        returner = [0, output_placeholder]
    elif isinstance(sig.output_type, ListType):
        returner = [0, output_placeholder]
    else:
        raise TypeCheckFailure(f"Invalid output type: {sig.output_type}")
    return output_placeholder, output_size, returner


def get_external_interface_keywords(stmt_expr, context):
    from vyper.parser.expr import Expr

    value, gas = None, None
    for kw in stmt_expr.keywords:
        if kw.arg == "gas":
            gas = Expr.parse_value_expr(kw.value, context)
        elif kw.arg == "value":
            value = Expr.parse_value_expr(kw.value, context)
        else:
            raise TypeCheckFailure("Unexpected keyword argument")
    return value, gas


def make_external_call(stmt_expr, context):
    from vyper.parser.expr import Expr

    value, gas = get_external_interface_keywords(stmt_expr, context)

    if isinstance(stmt_expr.func, vy_ast.Attribute) and isinstance(
        stmt_expr.func.value, vy_ast.Call
    ):
        contract_name = stmt_expr.func.value.func.id
        contract_address = Expr.parse_value_expr(stmt_expr.func.value.args[0], context)

        return external_call(
            stmt_expr,
            context,
            contract_name,
            contract_address,
            pos=getpos(stmt_expr),
            value=value,
            gas=gas,
        )

    elif (
        isinstance(stmt_expr.func.value, vy_ast.Attribute)
        and stmt_expr.func.value.attr in context.sigs
    ):  # noqa: E501
        contract_name = stmt_expr.func.value.attr
        var = context.globals[stmt_expr.func.value.attr]
        contract_address = unwrap_location(
            LLLnode.from_list(
                var.pos,
                typ=var.typ,
                location="storage",
                pos=getpos(stmt_expr),
                annotation="self." + stmt_expr.func.value.attr,
            )
        )

        return external_call(
            stmt_expr,
            context,
            contract_name,
            contract_address,
            pos=getpos(stmt_expr),
            value=value,
            gas=gas,
        )

    elif (
        isinstance(stmt_expr.func.value, vy_ast.Attribute)
        and stmt_expr.func.value.attr in context.globals
        and hasattr(context.globals[stmt_expr.func.value.attr].typ, "name")
    ):

        contract_name = context.globals[stmt_expr.func.value.attr].typ.name
        var = context.globals[stmt_expr.func.value.attr]
        contract_address = unwrap_location(
            LLLnode.from_list(
                var.pos,
                typ=var.typ,
                location="storage",
                pos=getpos(stmt_expr),
                annotation="self." + stmt_expr.func.value.attr,
            )
        )

        return external_call(
            stmt_expr,
            context,
            contract_name,
            contract_address,
            pos=getpos(stmt_expr),
            value=value,
            gas=gas,
        )

    else:
        raise StructureException("Unsupported operator.", stmt_expr)
