import argparse
import os
import logging
from py_gql import build_schema
from py_gql.lang.ast import UnionTypeDefinition
from copy import deepcopy

TAP_STRING = '    '


def mkdir_if_not_exist(path):
    if not os.path.exists(path):
        try:
            os.makedirs(path)
        except:
            raise Exception("mkdir failed: {}".format(path))


parser = argparse.ArgumentParser()
parser.add_argument('--schemaFilePath', type=str)
parser.add_argument('--destDirPath', type=str)
parser.add_argument('--depthLimit', type=int)
parser.add_argument('-C', '--includeDeprecatedFields', action='store_true')

args = parser.parse_args()

with open(args.schemaFilePath) as v:
    sdl = "".join(v.readlines())
    gql_schema = build_schema(sdl)

mkdir_if_not_exist(args.destDirPath)


def get_field_args_dict(field, duplicage_args_counts, all_args_dict=None):
    if all_args_dict is None:
        all_args_dict = {}
    field_args_dict = {}

    for argument in field.arguments:
        if argument.name in duplicage_args_counts:
            index = duplicage_args_counts[argument.name] + 1
            duplicage_args_counts[argument.name] = index
            field_args_dict["{}{}".format(
                argument.name,
                index
            )] = argument
        elif argument.name in all_args_dict:
            duplicage_args_counts[argument.name] = 1
            field_args_dict["{}1".format(argument.name)] = argument
        else:
            field_args_dict[argument.name] = argument
    return field_args_dict


def get_args_to_vars_str(dict):
    list = []
    for var_name, argument in dict.items():
        list.append(
            "{}: ${}".format(
                argument.name,
                var_name
            )
        )
    return ", ".join(list)


def get_vars_to_types_str(dict):
    list = []
    for var_name, argument in dict.items():
        list.append(
            "${}: {}".format(
                var_name,
                argument.type
            )
        )
    return ", ".join(list)


def generate_query(cur_name,
                   cur_parent_type,
                   cur_parent_name=None,
                   arguments_dict=None,
                   duplicate_args_counts=None,
                   cross_reference_key_list=None,
                   cur_depth=1):
    if arguments_dict is None:
        arguments_dict = {}
    if duplicate_args_counts is None:
        duplicate_args_counts = {}
    if cross_reference_key_list is None:
        cross_reference_key_list = []

    field = gql_schema.get_type(cur_parent_type).field_map.get(cur_name)
    cur_type_name = field.type.type.name if hasattr(field.type, "type") else field.type.name
    cur_type = gql_schema.get_type(cur_type_name)

    query_str = ""
    child_query = ''

    if hasattr(cur_type, "fields") and cur_type.fields:
        cross_reference_key = "{}To{}Key".format(cur_parent_name, cur_name)

        if cross_reference_key in cross_reference_key_list or cur_depth > args.depthLimit:
            return ''
        cross_reference_key_list.append(cross_reference_key)

        if cur_type.fields != NotImplemented:
            child_keys = map(lambda x: x.name, cur_type.fields)
            child_keys = filter(
                lambda field_name: args.includeDeprecatedFields or not cur_type.field_map.get(field_name).deprecated,
                child_keys
            )

            child_query_list = []
            for child_key in child_keys:
                res = generate_query(child_key, cur_type.name, cur_name,
                                     arguments_dict,
                                     duplicate_args_counts,
                                     deepcopy(cross_reference_key_list),
                                     cur_depth + 1)
                if "query_str" in res:
                    child_query_list.append(res.get("query_str"))

            child_query = "\n".join(child_query_list)

    if not ((hasattr(cur_type, "fields") and cur_type.fields != NotImplemented) and (not child_query)):
        query_str = TAP_STRING * cur_depth + field.name
        if field.arguments.__len__() > 0:
            field_args_dict = get_field_args_dict(field, duplicate_args_counts, arguments_dict)
            arguments_dict.update(field_args_dict)
            query_str += "({})".format(get_args_to_vars_str(field_args_dict))
        if child_query:
            query_str += "{{\n{}\n{}}}".format(
                child_query,
                TAP_STRING * cur_depth
            )

    if hasattr(cur_type, "nodes") and cur_type.nodes and isinstance(cur_type.nodes[0], UnionTypeDefinition):
        types = cur_type.types

        if types:
            indent = TAP_STRING * cur_depth
            frag_indent = TAP_STRING * (cur_depth + 1)
            query_str += "{\n"

            for value_type_name in types:
                value_type = gql_schema.get_type(value_type_name.name)

                union_child_query_list = []
                for cur in value_type.fields:
                    res = generate_query(cur.name, value_type.name, cur_name,
                                         arguments_dict,
                                         duplicate_args_counts,
                                         deepcopy(cross_reference_key_list),
                                         cur_depth + 2)
                    if "query_str" in res:
                        union_child_query_list.append(res.get("query_str"))

                    union_child_query = "\n".join(union_child_query_list)

                    query_str += "{}... on {} {{\n{}\n{}}}\n".format(
                        frag_indent, value_type_name.name, union_child_query, frag_indent
                    )
            query_str += indent + "}"

    return {
        'query_str': query_str,
        'arguments_dict': arguments_dict
    }


def generate_file(obj, description):
    if description == 'Mutation':
        output_folder_name = "mutations"
    elif description == "Query":
        output_folder_name = "queries"
    elif description == "Subscription":
        output_folder_name = "subscriptions"
    else:
        logging.warning("description is required")

    write_folder = os.path.join(args.destDirPath, output_folder_name)
    mkdir_if_not_exist(write_folder)

    for type in obj:
        field = gql_schema.get_type(description).field_map.get(type.name)
        if not args.includeDeprecatedFields and field.deprecated:
            continue
        query_result = generate_query(field.name, description)

        vars_to_types_str = get_vars_to_types_str(query_result["arguments_dict"])

        query = "{} {}{}{{\n{}\n}}".format(
            description.lower(),
            type.name,
            ('(' + vars_to_types_str + ')') if vars_to_types_str else '',
            query_result["query_str"]
        )

        with open(os.path.join(write_folder, '{}.gql'.format(type.name)), "w+") as v:
            v.write(query)


if gql_schema.mutation_type:
    generate_file(gql_schema.mutation_type.fields, 'Mutation')
else:
    logging.warning('No mutation type found in your schema')

if gql_schema.query_type:
    generate_file(gql_schema.query_type.fields, 'Query')
else:
    logging.warning('No query type found in your schema')

if gql_schema.subscription_type:
    generate_file(gql_schema.subscription_type.fields, 'Subscription')
    pass
else:
    logging.warning('No subscription type found in your schema')
