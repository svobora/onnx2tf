#! /usr/bin/env python

import os
import sys
import logging
import warnings

warnings.simplefilter(action='ignore', category=FutureWarning)
warnings.simplefilter(action='ignore', category=Warning)
import random
random.seed(0)
import numpy as np
np.random.seed(0)
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
import tensorflow as tf
tf.random.set_seed(0)
tf.get_logger().setLevel('INFO')
tf.autograph.set_verbosity(0)
tf.get_logger().setLevel(logging.ERROR)

import onnx
import onnx_graphsurgeon as gs
from typing import Optional, List
from argparse import ArgumentParser

import importlib
from utils.colors import Color

def convert(
    input_onnx_file_path: Optional[str] = '',
    onnx_graph: Optional[onnx.ModelProto] = None,
    output_folder_path: Optional[str] = 'saved_model',
    keep_nchw_or_ncdhw_input_names: Optional[List[str]] = None,
    replace_argmax_to_reducemax_and_indicies_is_int64: Optional[bool] = False,
    replace_argmax_to_reducemax_and_indicies_is_float32: Optional[bool] = False,
    non_verbose: Optional[bool] = False,
) -> tf.keras.Model:
    """Convert ONNX to TensorFlow models.

    Parameters
    ----------
    input_onnx_file_path: Optional[str]
        Input onnx file path.\n
        Either input_onnx_file_path or onnx_graph must be specified.

    onnx_graph: Optional[onnx.ModelProto]
        onnx.ModelProto.\n
        Either input_onnx_file_path or onnx_graph must be specified.\n
        onnx_graph If specified, ignore input_onnx_file_path and process onnx_graph.

    output_folder_path: Optional[str]
        Output tensorflow model folder path.\n
        Default: "saved_model"

    keep_nchw_or_ncdhw_input_names: Optional[List[str]]
        Holds the NCHW or NCDHW of the input shape for the specified INPUT OP names.\n
        If a nonexistent INPUT OP name is specified, it is ignored.\n
        Valid only for 4D and 5D input tensors.\n\n
        e.g. \n
        --keep_nchw_or_ncdhw_input_names=['input0', 'input1', 'input2']

    replace_argmax_to_reducemax_and_indicies_is_int64: Optional[bool]
        Replace ArgMax with a ReduceMax. The returned indicies are int64.\n
        Default: False

    replace_argmax_to_reducemax_and_indicies_is_float32: Optional[bool]
        Replace ArgMax with a ReduceMax. The returned indicies are float32.\n
        Only one of replace_argmax_to_reducemax_and_indicies_is_int64 and \n
        replace_argmax_to_reducemax_and_indicies_is_float32 can be specified.\n
        Default: False

    non_verbose: Optional[bool]
        Do not show all information logs. Only error logs are displayed.\n
        Only one of replace_argmax_to_reducemax_and_indicies_is_int64 and \n
        replace_argmax_to_reducemax_and_indicies_is_float32 can be specified.\n
        Default: False

    Returns
    ----------
    model: tf.keras.Model
        Model
    """

    # Either designation required
    if not input_onnx_file_path and not onnx_graph:
        print(
            f'{Color.RED}ERROR:{Color.RESET} '+
            f'One of input_onnx_file_path or onnx_graph must be specified.'
        )
        sys.exit(1)

    # If output_folder_path is empty, set the initial value
    if not output_folder_path:
        output_folder_path = 'saved_model'

    # Escape
    input_onnx_file_path = fr'{input_onnx_file_path}'
    output_folder_path = fr'{output_folder_path}'

    # Input file existence check
    if not os.path.exists(input_onnx_file_path):
        print(
            f'{Color.RED}ERROR:{Color.RESET} ' +
            f'The specified *.onnx file does not exist. ' +
            f'input_onnx_file_path: {input_onnx_file_path}'
        )
        sys.exit(1)

    # Create Output folder
    os.makedirs(output_folder_path, exist_ok=True)


    # Loading Graphs
    # onnx_graph If specified, onnx_graph is processed first
    if not onnx_graph:
        onnx_graph = onnx.load(input_onnx_file_path)
    graph = gs.import_onnx(onnx_graph)

    # Define additional parameters
    additional_parameters = {
        'replace_argmax_to_reducemax_and_indicies_is_int64': replace_argmax_to_reducemax_and_indicies_is_int64,
        'replace_argmax_to_reducemax_and_indicies_is_float32': replace_argmax_to_reducemax_and_indicies_is_float32,
    }

    tf_layers_dict = {}

    with graph.node_ids():

        # Inputs
        for graph_input in graph.inputs:
            """
            graph_input.shape: [1]
            graph_input.dtype: dtype('float32')
            graph_input.name: 'abs6_input'

            graph_input.shape: [1, 3, 192, 256]
            graph_input.dtype: dtype('float32')
            graph_input.name: 'input'

            graph_input.shape: [1, 3, 'height', 'width']
            graph_input.dtype: dtype('float32')
            graph_input.name: 'input'
            """
            op = importlib.import_module(f'ops.Input')
            op.make_node(
                graph_input=graph_input,
                tf_layers_dict=tf_layers_dict,
                keep_nchw_or_ncdhw_input_names=keep_nchw_or_ncdhw_input_names,
                **additional_parameters,
            )

        # Nodes
        # https://github.com/onnx/onnx/blob/main/docs/Operators.md
        for graph_node in graph.nodes:
            optype = graph_node.op
            op = importlib.import_module(f'ops.{optype}')
            op.make_node(
                graph_node=graph_node,
                tf_layers_dict=tf_layers_dict,
                **additional_parameters,
            )

        # List "optype"="Input"
        input_names = [
            graph_input.name for graph_input in graph.inputs
        ]
        inputs = [
            layer_info['tf_node'] \
                for opname, layer_info in tf_layers_dict.items() \
                    if opname in input_names
        ]

        # List Output
        output_names = [
            graph_output.name for graph_output in graph.outputs
        ]
        outputs = [
            layer_info['tf_node'] \
                for opname, layer_info in tf_layers_dict.items() \
                    if opname in output_names
        ]

        model = tf.keras.Model(inputs=inputs, outputs=outputs)
        model.summary()

        # Create concrete func
        run_model = tf.function(lambda *inputs : model(inputs))
        concrete_func = run_model.get_concrete_function(
            *[tf.TensorSpec(tensor.shape, tensor.dtype) for tensor in model.inputs]
        )

        # saved_model
        tf.saved_model.save(concrete_func, output_folder_path)

        # TFLite
        converter = tf.lite.TFLiteConverter.from_concrete_functions(
            [concrete_func]
        )
        converter.target_spec.supported_ops = [
            tf.lite.OpsSet.TFLITE_BUILTINS,
            tf.lite.OpsSet.SELECT_TF_OPS,
        ]
        tflite_model = converter.convert()
        with open(f'{output_folder_path}/model_float32.tflite', 'wb') as w:
            w.write(tflite_model)

        return model


def main():
    parser = ArgumentParser()
    parser.add_argument(
        '-i',
        '--input_onnx_file_path',
        type=str,
        required=True,
        help='Input onnx file path.'
    )
    parser.add_argument(
        '-o',
        '--output_folder_path',
        type=str,
        help=\
            'Output folder path. \n' +
            'Default: "saved_model"'
    )
    parser.add_argument(
        '-k',
        '--keep_nchw_or_ncdhw_input_names',
        type=str,
        nargs='+',
        help=\
            'Holds the NCHW or NCDHW of the input shape for the specified INPUT OP names. \n' +
            'If a nonexistent INPUT OP name is specified, it is ignored. \n' +
            'Valid only for 4D and 5D input tensors. \n\n' +
            'e.g. \n' +
            '--keep_nchw_or_ncdhw_input_names "input0" "input1" "input2"'
    )
    rar_group = parser.add_mutually_exclusive_group()
    rar_group.add_argument(
        '-rari64',
        '--replace_argmax_to_reducemax_and_indicies_is_int64',
        action='store_true',
        help=\
            'Replace ArgMax with a ReduceMax. The returned indicies are int64. \n' +
            'Only one of replace_argmax_to_reducemax_and_indicies_is_int64 and \n' +
            'replace_argmax_to_reducemax_and_indicies_is_float32 can be specified.'
    )
    rar_group.add_argument(
        '-rarf32',
        '--replace_argmax_to_reducemax_and_indicies_is_float32',
        action='store_true',
        help=\
            'Replace ArgMax with a ReduceMax. The returned indicies are float32. \n' +
            'Only one of replace_argmax_to_reducemax_and_indicies_is_int64 and \n' +
            'replace_argmax_to_reducemax_and_indicies_is_float32 can be specified.'
    )
    parser.add_argument(
        '-n',
        '--non_verbose',
        action='store_true',
        help='Do not show all information logs. Only error logs are displayed.'
    )
    args = parser.parse_args()

    # Convert
    model = convert(
        input_onnx_file_path=args.input_onnx_file_path,
        output_folder_path=args.output_folder_path,
        keep_nchw_or_ncdhw_input_names=args.keep_nchw_or_ncdhw_input_names,
        replace_argmax_to_reducemax_and_indicies_is_int64=args.replace_argmax_to_reducemax_and_indicies_is_int64,
        replace_argmax_to_reducemax_and_indicies_is_float32=args.replace_argmax_to_reducemax_and_indicies_is_float32,
        non_verbose=args.non_verbose,
    )


if __name__ == '__main__':
    main()
